#!/bin/sh

/usr/bin/env python2.4 setup.py py2app --dist-dir . -A "$@" && Miro.app/Contents/MacOS/Miro
