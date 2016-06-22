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

    TRACKING = ["track", "tracking"]
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


    def cmd_show(self, event, action):
        """Show information on projects or projects being tracked.
        Show which projects are being tracked. 'prometheus show tracking'
        Show which proejcts are recognised so they could be tracked. 'prometheus show projects'
        """
        if action in self.TRACKING:
            return self._get_tracking(event["room_id"])
        elif action == "projects":
            projects = self.store.get("known_projects")
            return "Available projects: %s" % json.dumps(projects)
        else:
            return "Invalid arg '%s'.\n %s" % (action, self.cmd_show.__doc__)

    @admin_only
    def cmd_track(self, event, *args):
        """Track projects. 'prometheus track Foo "bar with spaces"'"""
        if len(args) == 0:
            return self._get_tracking(event["room_id"])

        for project in args:
            if not project in self.store.get("known_projects"):
                return "Unknown project name: %s." % project

        self._send_track_event(event["room_id"], args)

        return "Prometheus notifications for projects %s will be displayed when they fail." % (args)

    @admin_only
    def cmd_add(self, event, project):
        """Add a project for tracking. 'prometheus add projectName'"""
        if project not in self.store.get("known_projects"):
            return "Unknown project name: %s." % project

        try:
            room_projects = self.rooms.get_content(
                event["room_id"],
                PrometheusPlugin.TYPE_TRACK)["projects"]
        except KeyError:
            room_projects = []

        if project in room_projects:
            return "%s is already being tracked." % project

        room_projects.append(project)
        self._send_track_event(event["room_id"], room_projects)

        return "Added %s. Prometheus notifications for projects %s will be displayed when they fail." % (project, room_projects)

    @admin_only
    def cmd_remove(self, event, project):
        """Remove a project from tracking. 'prometheus remove projectName'"""
        try:
            room_projects = self.rooms.get_content(
                event["room_id"],
                PrometheusPlugin.TYPE_TRACK)["projects"]
        except KeyError:
            room_projects = []

        if project not in room_projects:
            return "Cannot remove %s : It isn't being tracked." % project

        room_projects.remove(project)
        self._send_track_event(event["room_id"], room_projects)

        return "Removed %s. Prometheus notifications for projects %s will be displayed when they fail." % (project, room_projects)

    @admin_only
    def cmd_stop(self, event, action):
        """Stop tracking projects. 'prometheus stop tracking'"""
        if action in self.TRACKING:
            self._send_track_event(event["room_id"], [])
            return "Stopped tracking projects."
        else:
            return "Invalid arg '%s'.\n %s" % (action, self.cmd_stop.__doc__)

    def _get_tracking(self, room_id):
        try:
            return ("Currently tracking %s" %
                json.dumps(self.rooms.get_content(
                    room_id, PrometheusPlugin.TYPE_TRACK)["projects"]
                )
            )
        except KeyError:
            return "Not tracking any projects currently."

    def _send_track_event(self, room_id, project_names):
        self.matrix.send_state_event(
            room_id,
            self.TYPE_TRACK,
            {
                "projects": project_names
            }
        )

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
                    try:
                        if (len(self.rooms.get_content(
                                room_id, PrometheusPlugin.TYPE_TRACK)["projects"])):
                            log.debug("queued message for room " + room_id + " at " + str(self.queue_counter) + ": %s", alert)
                            queue.put((self.queue_counter, room_id, template.render(alert)))
                            self.queue_counter += 1
                    except KeyError:
                        pass
        # try version 1 format
        process_alerts(json_data.get("alert", []), Template(self.store.get("v1_message_template")))
        # try version 2+ format
        process_alerts(json_data.get("alerts", []), Template(self.store.get("v2_message_template")))


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
