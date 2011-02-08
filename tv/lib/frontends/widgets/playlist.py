# Miro - an RSS based video player application
# Copyright (C) 2005, 2006, 2007, 2008, 2009, 2010, 2011
# Participatory Culture Foundation
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

"""playlist.py -- Handle displaying a playlist."""

import itertools

from miro import messages
from miro import signals
from miro.gtcache import gettext as _
from miro.plat.frontends.widgets import widgetset
from miro.frontends.widgets import itemcontextmenu
from miro.frontends.widgets import itemlist
from miro.frontends.widgets import itemlistcontroller
from miro.frontends.widgets import itemlistwidgets
from miro.frontends.widgets import style
from miro.frontends.widgets.widgetstatestore import WidgetStateStore

class DropHandler(signals.SignalEmitter):
    def __init__(self, playlist_id, item_view, sorter):
        signals.SignalEmitter.__init__(self)
        self.create_signal('new-order')
        self.playlist_id = playlist_id
        self.item_view = item_view
        self.sorter = sorter

    def allowed_actions(self):
        return widgetset.DRAG_ACTION_MOVE

    def allowed_types(self):
        return ('downloaded-item',)

    def validate_drop(self, table_view, model, typ, source_actions, parent,
            position):
        if position != -1 and typ == 'downloaded-item':
            return widgetset.DRAG_ACTION_MOVE
        return widgetset.DRAG_ACTION_NONE

    def accept_drop(self, table_view, model, typ, source_actions, parent,
            position, data):
        dragged_ids = set([int(id) for id in data.split('-')])
        if 0 <= position < len(model):
            insert_info = model.nth_row(position)[0]
        else:
            insert_info = None
        self.item_view.item_list.set_sort(None)
        try:
            self.item_view.item_list.move_items(insert_info, dragged_ids)
        finally:
            self.item_view.model_changed()
        new_order = [info.id for info in model.info_list()]
        self.sorter.set_new_order(new_order)
        self.item_view.item_list.set_sort(self.sorter)
        self.emit('new-order', new_order)
        return True

class PlaylistSort(itemlist.ItemSort):
    """Sort that orders items by their order in the playlist.
    """

    def __init__(self):
        itemlist.ItemSort.__init__(self, True)
        self.positions = {}
        self.current_postion = itertools.count()

    def add_items(self, item_list):
        for item in item_list:
            if item.id not in self.positions:
                self.positions[item.id] = self.current_postion.next()

    def forget_items(self, id_list):
        for id in id_list:
            del self.positions[id]

    def set_new_order(self, id_order):
        self.positions = dict((id, self.current_postion.next())
            for id in id_order)

    def sort_key(self, item):
        return self.positions[item.id]

class PlaylistStandardView(itemlistwidgets.StandardView):
    def __init__(self, item_list, scroll_pos, selection, playlist_id):
        itemlistwidgets.StandardView.__init__(self, item_list,
                scroll_pos, selection)
        self.playlist_id = playlist_id

    def build_renderer(self):
        return style.PlaylistItemRenderer(display_channel=True)

class PlaylistView(itemlistcontroller.SimpleItemListController):
    image_filename = 'playlist-icon.png'

    def __init__(self, playlist_info):
        self.type = u'playlist'
        self.id = playlist_info.id
        self.title = playlist_info.name
        self.is_folder = playlist_info.is_folder
        itemlistcontroller.SimpleItemListController.__init__(self)

    def make_sorters(self):
        self.multiview_sorter = PlaylistSort()

    def build_standard_view(self, scroll_pos, selection):
        standard_view = PlaylistStandardView(self.item_list,
                scroll_pos, selection, self.id)
        return standard_view, standard_view

    def make_drop_handler(self):
        standard_view_type = WidgetStateStore.get_standard_view_type()
        standard_view = self.views[standard_view_type]
        handler = DropHandler(self.id, standard_view, self.multiview_sorter)
        handler.connect('new-order', self._on_new_order)
        return handler

    def make_context_menu_handler(self):
        if self.is_folder:
            return itemcontextmenu.ItemContextMenuHandlerPlaylistFolder()
        else:
            return itemcontextmenu.ItemContextMenuHandlerPlaylist(self.id)

    def handle_delete(self):
        selected = [info.id for info in self.get_selection()]
        m = messages.RemoveVideosFromPlaylist(self.id, selected)
        m.send_to_backend()
        return True

    def build_widget(self):
        itemlistcontroller.SimpleItemListController.build_widget(self)
        text = _('This Playlist is Empty')
        self.widget.list_empty_mode_vbox.pack_start(
                itemlistwidgets.EmptyListHeader(text))
        text = _('To add an item, drag it onto the name of this playlist '
                'in the sidebar.')
        self.widget.list_empty_mode_vbox.pack_start(
                itemlistwidgets.EmptyListDescription(text))

    def check_for_empty_list(self):
        list_empty = (self.item_list.get_count() == 0)
        self.widget.set_list_empty_mode(list_empty)

    def _on_new_order(self, drop_handler, order):
        messages.PlaylistReordered(self.id, order).send_to_backend()
