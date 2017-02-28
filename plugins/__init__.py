# -*- coding: utf-8 -*-

from os.path import dirname, basename, isfile

import glob


# load all plugins in this directory, except this file (__init__.py)
modules = glob.glob(dirname(__file__) + "/*.py")
__all__ = [basename(f)[:-3] for f in modules if isfile(f) and f.find("__init__") == -1]
