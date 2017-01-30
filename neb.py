#!/usr/bin/env python
import argparse

from matrix_client.api import MatrixHttpApi
from neb.engine import Engine
from neb.matrix import MatrixConfig
from neb.plugins import PluginInterface, Plugin
from plugins import *  # Appears unused, but is used to dynamically load the plugins

import logging
import logging.handlers
import time

log = logging.getLogger(name=__name__)

# TODO:
# - Add utility plugins in neb package to do things like "invite x to room y"?
# - Add other plugins as tests of plugin architecture (e.g. anagrams, dictionary lookup, etc)


def generate_config(url, username, token, config_loc):
    config = MatrixConfig(
            hs_url=url,
            user_id=username,
            access_token=token,
            admins=[]
    )
    save_config(config_loc, config)
    return config


def save_config(loc, config):
    with open(loc, 'w') as f:
        MatrixConfig.to_file(config, f)


def load_config(loc):
    try:
        with open(loc, 'r') as f:
            return MatrixConfig.from_file(f)
    except:
        pass


def configure_logging(logfile):
    log_format = "%(asctime)s %(levelname)s: %(message)s"
    logging.basicConfig(
        level=logging.DEBUG,
        format=log_format
    )

    if logfile:
        formatter = logging.Formatter(log_format)

        # rotate logs (20MB, max 6 = 120MB)
        handler = logging.handlers.RotatingFileHandler(
              logfile, maxBytes=(1000 * 1000 * 20), backupCount=5)
        handler.setFormatter(formatter)
        logging.getLogger('').addHandler(handler)

def get_subclasses(cls):
    subclasses = []
    for subclass in cls.__subclasses__():
        subclasses.append(subclass)
        subclasses.extend(get_subclasses(subclass))
    return subclasses

def main(config):
    # setup api/endpoint
    matrix = MatrixHttpApi(config.base_url, config.token)

    log.debug("Setting up plugins...")
    plugins = get_subclasses(PluginInterface)
    plugins = [p for p in plugins if p != Plugin]  # filter out the Plugin abstract class

    # setup engine
    engine = Engine(matrix, config)
    for plugin in plugins:
        engine.add_plugin(plugin)

    engine.setup()

    while True:
        try:
            log.info("Listening for incoming events.")
            engine.event_loop()
        except Exception as e:
            log.error("Ruh roh: %s", e)
        time.sleep(5)

    log.info("Terminating.")


if __name__ == '__main__':
    a = argparse.ArgumentParser("Runs NEB. See plugins for commands.")
    a.add_argument(
        "-c", "--config", dest="config",
        help="The config to create or read from."
    )
    a.add_argument(
        "-l", "--log-file", dest="log",
        help="Log to this file."
    )
    args = a.parse_args()

    configure_logging(args.log)
    log.info("  ===== NEB initialising ===== ")

    config = None
    if args.config:
        log.info("Loading config from %s", args.config)
        config = load_config(args.config)
        if not config:
            log.info("Setting up for an existing account.")
            print "Config file could not be loaded."
            print ("NEB works with an existing Matrix account. "
                "Please set up an account for NEB if you haven't already.'")
            print "The config for this account will be saved to '%s'" % args.config
            hsurl = raw_input("Home server URL (e.g. http://localhost:8008): ").strip()
            if hsurl.endswith("/"):
                hsurl = hsurl[:-1]
            hsurl = hsurl + "/_matrix/client/api/v1"  # v1 compatibility
            username = raw_input("Full user ID (e.g. @user:domain): ").strip()
            token = raw_input("Access token: ").strip()
            config = generate_config(hsurl, username, token, args.config)
    else:
        a.print_help()
        print "You probably want to run 'python neb.py -c neb.config'"

    if config:
        main(config)
