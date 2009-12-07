# Miro - an RSS based video player application
# Copyright (C) 2005-2009 Participatory Culture Foundation
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301 USA
#
# In addition, as a special exception, the copyright holders give
# permission to link the code of portions of this program with the OpenSSL
# library.
#
# You must obey the GNU General Public License in all respects for all of
# the code used other than OpenSSL. If you modify file(s) with this
# exception, you may extend this exception to your version of the file(s),
# but you are not obligated to do so. If you do not wish to do so, delete
# this exception statement from your version. If you delete this exception
# statement from all source files in the program, then also delete it here.

"""menus.py -- Menu handling code."""

import struct
import logging

from objc import nil
from AppKit import *
from Foundation import *

from miro import app, config, prefs

from miro.gtcache import gettext as _
from miro.frontends.widgets import menus
from miro.frontends.widgets.menus import MOD, CTRL, ALT, SHIFT, CMD, RIGHT_ARROW, LEFT_ARROW, UP_ARROW, DOWN_ARROW, SPACE, ENTER, DELETE, BKSPACE, ESCAPE
from miro.plat.frontends.widgets import wrappermap

STD_ACTION_MAP = {
    "HideMiro":         (NSApp(), 'hide:'),
    "HideOthers":       (NSApp(), 'hideOtherApplications:'),
    "ShowAll":          (NSApp(), 'unhideAllApplications:'),
    "Cut":              (nil,     'cut:'),
    "Copy":             (nil,     'copy:'),
    "Paste":            (nil,     'paste:'),
    "Delete":           (nil,     'delete:'),
    "SelectAll":        (nil,     'selectAll:'),
    "Zoom":             (nil,     'performZoom:'),
    "Minimize":         (nil,     'performMiniaturize:'),
    "BringAllToFront":  (nil,     'arrangeInFront:'),
    "CloseWindow":      (nil,     'performClose:'),
}

menus.set_mod(CMD)

MODIFIERS_MAP = {
    CMD:   NSCommandKeyMask,
    SHIFT: NSShiftKeyMask,
    CTRL:  NSControlKeyMask,
    ALT:   NSAlternateKeyMask
}

if isinstance(NSBackspaceCharacter, int):
    backspace = NSBackspaceCharacter
else:
    backspace = ord(NSBackspaceCharacter)
    
KEYS_MAP = {
    SPACE: " ",
    BKSPACE: struct.pack("H", backspace),
    DELETE: NSDeleteFunctionKey,
    RIGHT_ARROW: NSRightArrowFunctionKey,
    LEFT_ARROW: NSLeftArrowFunctionKey,
    UP_ARROW: NSUpArrowFunctionKey,
    DOWN_ARROW: NSDownArrowFunctionKey,
    '.': '.',
    ',': ','
}

REVERSE_MODIFIERS_MAP = dict((i[1], i[0]) for i in MODIFIERS_MAP.items())
REVERSE_KEYS_MAP = dict((i[1], i[0]) for i in KEYS_MAP.items() 
        if i[0] != BKSPACE)
REVERSE_KEYS_MAP[u'\x7f'] = BKSPACE
REVERSE_KEYS_MAP[u'\x1b'] = ESCAPE

def make_modifier_mask(shortcut):
    mask = 0
    for modifier in shortcut.modifiers:
        mask |= MODIFIERS_MAP[modifier]
    return mask

def make_menu_item(menu_item):
    nsmenuitem = NSMenuItem.alloc().init()
    nsmenuitem.setTitleWithMnemonic_(menu_item.label.replace("_", "&"))
    if isinstance(menu_item, menus.MenuItem):
        for shortcut in menu_item.shortcuts:
            if isinstance(shortcut.shortcut, str):
                nsmenuitem.setKeyEquivalent_(shortcut.shortcut)
                nsmenuitem.setKeyEquivalentModifierMask_(make_modifier_mask(shortcut))
                continue
            else:
                if shortcut.shortcut in KEYS_MAP:
                    nsmenuitem.setKeyEquivalent_(KEYS_MAP[shortcut.shortcut])
                    nsmenuitem.setKeyEquivalentModifierMask_(make_modifier_mask(shortcut))
                    continue

        if menu_item.action in STD_ACTION_MAP:
            nsmenuitem.setTarget_(STD_ACTION_MAP[menu_item.action][0])
            nsmenuitem.setAction_(STD_ACTION_MAP[menu_item.action][1])
        else:
            nsmenuitem.setRepresentedObject_(menu_item.action)
            nsmenuitem.setTarget_(NSApp().delegate())
            nsmenuitem.setAction_('handleMenuItem:')
    return nsmenuitem

def populate_single_menu(nsmenu, miro_menu):
    for miro_item in miro_menu.menuitems:
        if isinstance(miro_item, menus.Separator):
            item = NSMenuItem.separatorItem()
        elif isinstance(miro_item, menus.MenuItem):
            item = make_menu_item(miro_item)
        elif isinstance(miro_item, menus.Menu):
            submenu = NSMenu.alloc().init()
            populate_single_menu(submenu, miro_item)
            item = NSMenuItem.alloc().init()
            item.setTitle_(miro_item.label.replace("_", ""))
            item.setSubmenu_(submenu)
        nsmenu.addItem_(item)

def populate_menu():
    short_appname = config.get(prefs.SHORT_APP_NAME)

    menubar = menus.get_menu()
    menubar.get("VideoMenu").label = _("_File")

    # Application menu
    miroMenuItems = [
        menubar.get("About"),
        menus.Separator(),
        menubar.get("Donate"),
        menubar.get("CheckVersion"),
        menus.Separator(),
        menubar.get("EditPreferences"),
        menus.Separator(),
        menus.MenuItem(_("Services"), "ServicesMenu"),
        menus.Separator(),
        menus.MenuItem(_("Hide %(appname)s", {"appname": short_appname}),
                       "HideMiro", menus.Shortcut("h", MOD)),
        menus.MenuItem(_("Hide Others"), "HideOthers", 
                       menus.Shortcut("h", MOD, ALT)),
        menus.MenuItem(_("Show All"), "ShowAll"),
        menus.Separator(),
        menubar.get("Quit")
    ]
    miroMenu = menus.Menu(short_appname, "Miro", miroMenuItems)
    miroMenu.get("EditPreferences").label = _("Preferences...")
    miroMenu.get("EditPreferences").shortcuts = (menus.Shortcut(",", MOD),)
    miroMenu.get("Quit").label = _("Quit %(appname)s", 
                                   {"appname": short_appname})

    # File menu
    closeWinItem = menus.MenuItem(_("Close Window"), "CloseWindow", 
                                  menus.Shortcut("w", MOD))
    menubar.get("VideoMenu").append(closeWinItem)

    # Edit menu
    editMenuItems = [
        menus.MenuItem(_("Cut"), "Cut", menus.Shortcut("x", MOD)),
        menus.MenuItem(_("Copy"), "Copy", menus.Shortcut("c", MOD)),
        menus.MenuItem(_("Paste"), "Paste", menus.Shortcut("v", MOD)),
        menus.MenuItem(_("Delete"), "Delete"),
        menus.Separator(),
        menus.MenuItem(_("Select All"), "SelectAll", menus.Shortcut("a", MOD))
    ]
    editMenu = menus.Menu(_("Edit"), "Edit", editMenuItems)
    menubar.insert(1, editMenu)

    # Playback menu
    presentMenuItems = [
        menus.MenuItem(_("Present Half Size"), "PresentHalfSize", 
                       menus.Shortcut("0", MOD),
                       groups=["PlayingVideo", "PlayableVideosSelected"]),
        menus.MenuItem(_("Present Actual Size"), "PresentActualSize", 
                       menus.Shortcut("1", MOD),
                       groups=["PlayingVideo", "PlayableVideosSelected"]),
        menus.MenuItem(_("Present Double Size"), "PresentDoubleSize", 
                       menus.Shortcut("2", MOD),
                       groups=["PlayingVideo", "PlayableVideosSelected"]),
    ]
    presentMenu = menus.Menu(_("Present Video"), "Present", presentMenuItems)
    menubar.get("PlaybackMenu").append(presentMenu)

    # Window menu
    windowMenuItems = [
        menus.MenuItem(_("Zoom"), "Zoom"),
        menus.MenuItem(_("Minimize"), "Minimize", menus.Shortcut("m", MOD)),
        menus.Separator(),
        menus.MenuItem(_("Main Window"), "ShowMain", 
                       menus.Shortcut("M", MOD, SHIFT)),
        menus.Separator(),
        menus.MenuItem(_("Bring All to Front"), "BringAllToFront"),
    ]
    windowMenu = menus.Menu(_("Window"), "Window", windowMenuItems)
    menubar.insert(5, windowMenu)

    # Help Menu
    helpItem = menubar.get("Help")
    helpItem.label = _("%(appname)s Help", {"appname": short_appname})
    helpItem.shortcuts = (menus.Shortcut("?", MOD),)

    # Now populate the main menu bar
    main_menu = NSApp().mainMenu()
    appMenu = main_menu.itemAtIndex_(0).submenu()
    populate_single_menu(appMenu, miroMenu)

    for menu in menubar.menuitems:
        nsmenu = NSMenu.alloc().init()
        nsmenu.setTitle_(menu.label.replace("_", ""))
        populate_single_menu(nsmenu, menu)
        nsmenuitem = make_menu_item(menu)
        nsmenuitem.setSubmenu_(nsmenu)
        main_menu.addItem_(nsmenuitem)

    # we do this to get groups correct
    menubar.insert(0, miroMenu)

    menus.osx_menu_structure = menubar
    menus.osx_action_groups = menus.generate_action_groups(menubar)
    
class ContextMenuHandler(NSObject):
    def initWithCallback_(self, callback):
        self = super(ContextMenuHandler, self).init()
        self.callback = callback
        return self

    def handleMenuItem_(self, sender):
        self.callback()

class MiroContextMenu(NSMenu):
    # Works exactly like NSMenu, except it keeps a reference to the menu
    # handler objects.
    def init(self):
        self = super(MiroContextMenu, self).init()
        self.handlers = set()
        return self

    def addItem_(self, item):
        if isinstance(item.target(), ContextMenuHandler):
            self.handlers.add(item.target())
        return NSMenu.addItem_(self, item)

def make_context_menu(menu_items):
    nsmenu = MiroContextMenu.alloc().init()
    for item in menu_items:
        if item is None:
            nsitem = NSMenuItem.separatorItem()
        else:
            label, callback = item
            nsitem = NSMenuItem.alloc().init()
            if isinstance(label, tuple) and len(label) == 2:
                label, icon_path = label
                image = NSImage.alloc().initWithContentsOfFile_(icon_path)
                nsitem.setImage_(image)
            if callback is None:
                font_size = NSFont.systemFontSize()
                font = NSFont.fontWithName_size_("Lucida Sans Italic", font_size)
                if font is None:
                    font = NSFont.systemFontOfSize_(font_size)
                attributes = {NSFontAttributeName: font}
                attributed_label = NSAttributedString.alloc().initWithString_attributes_(label, attributes)
                nsitem.setAttributedTitle_(attributed_label)
            else:
                nsitem.setTitle_(label)
                if isinstance(callback, list):
                    submenu = make_context_menu(callback)
                    nsmenu.setSubmenu_forItem_(submenu, nsitem)
                else:
                    handler = ContextMenuHandler.alloc().initWithCallback_(callback)
                    nsitem.setTarget_(handler)
                    nsitem.setAction_('handleMenuItem:')
        nsmenu.addItem_(nsitem)
    return nsmenu

def translate_event_modifiers(event):
    mods = set()
    flags = event.modifierFlags()
    if flags & NSCommandKeyMask:
        mods.add(CMD)
    if flags & NSAlternateKeyMask:
        mods.add(ALT)
    if flags & NSShiftKeyMask:
        mods.add(SHIFT)
    return mods
