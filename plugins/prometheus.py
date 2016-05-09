# -*- coding: utf-8 -*-
from jinja2 import Template
import json
from matrix_client.api import MatrixRequestError
from neb.engine import KeyValueStore, RoomContextStore
from neb.plugins import Plugin, admin_only
from Queue import PriorityQueue
from threading import Thread


import time
import logging as log

queue = PriorityQueue()


class PrometheusPlugin(Plugin):
    """Plugin for interacting with Prometheus.
    """
    name = "prometheus"

    #Webhooks:
        #    /neb/prometheus
    TYPE_TRACK = "org.matrix.neb.plugin.prometheus.projects.tracking"


    def __init__(self, *args, **kwargs):
        super(PrometheusPlugin, self).__init__(*args, **kwargs)
        self.store = KeyValueStore("prometheus.json")
        self.rooms = RoomContextStore(
            [PrometheusPlugin.TYPE_TRACK]
        )
        self.queue_counter = 1L
        self.consumer = MessageConsumer(self.matrix)
        self.consumer.daemon = True
        self.consumer.start()

    def on_event(self, event, event_type):
        self.rooms.update(event)

    def on_sync(self, sync):
        log.debug("Plugin: Prometheus sync state:")
        self.rooms.init_from_sync(sync)

    def get_webhook_key(self):
        return "prometheus"

    def on_receive_webhook(self, url, data, ip, headers):
        json_data = json.loads(data)
        log.info("recv %s", json_data)
        def process_alerts(alerts, template):
            for alert in alerts:
                for room_id in self.rooms.get_room_ids():
                    log.debug("queued message for room " + room_id + " at " + str(self.queue_counter) + ": %s", alert)
                    queue.put((self.queue_counter, room_id, template.render(alert)))
                    self.queue_counter += 1
        # try version 1 format
        process_alerts(json_data.get("alert", []), Template(self.store.get("v1_message_template"))
        # try version 2+ format
        process_alerts(json_data.get("alerts", []), Template(self.store.get("v2_message_template"))


class MessageConsumer(Thread):
    """ This class consumes the produced messages
        also will try to resend the messages that
        are failed for instance when the server was down.
    """

    INITIAL_TIMEOUT_S = 5
    TIMEOUT_INCREMENT_S = 5
    MAX_TIMEOUT_S = 60 * 5

    def __init__(self, matrix):
        super(MessageConsumer, self).__init__()
        self.matrix = matrix

    def run(self):
        timeout = self.INITIAL_TIMEOUT_S

        log.debug("Starting consumer thread")
        while True:
            priority, room_id, message = queue.get()
            log.debug("Popped message for room " + room_id + " at position " + str(priority) + ": %s", message)
            try:
                self.send_message(room_id, message)
                timeout = self.INITIAL_TIMEOUT_S
            except Exception as e:
                log.debug("Failed to send message: %s", e)
                queue.put((priority, room_id, message))

                time.sleep(timeout)
                timeout += self.TIMEOUT_INCREMENT_S
                if timeout > self.MAX_TIMEOUT_S:
                    timeout = self.MAX_TIMEOUT_S

    def send_message(self, room_id, message):
        try:
            self.matrix.send_message_event(
                    room_id,
                    "m.room.message",
                    self.matrix.get_html_body(message, msgtype="m.notice"),
            )
        except KeyError:
            log.error(KeyError)
        except MatrixRequestError as e:
            if 400 <= e.code < 500:
                log.error("Matrix ignored message %s", e)
            else:
                raise
