from copy import copy
from datetime import datetime, timedelta
from gettext import gettext as _
from math import ceil
from xhtmltools import unescape,xhtmlify
from xml.sax.saxutils import unescape
import locale
import os
import shutil
import traceback

from feedparser import FeedParserDict

from database import DDBObject, defaultDatabase
from downloader import DownloaderFactory
from iconcache import IconCache
from templatehelper import escape
import config
import dialogs
import eventloop
import prefs
import resource
import views


_charset = locale.getpreferredencoding()

##
# An item corresponds to a single entry in a feed. Generally, it has
# a single url associated with it
class Item(DDBObject):
    manualDownloads = defaultDatabase.filter(lambda x:isinstance(x,Item) and x.getState() == "downloading" and not x.getAutoDownloaded())

    def __init__(self, feed_id, entry, linkNumber = 0):
        self.feed_id = feed_id
        self.seen = False
        self.downloaders = []
        self.autoDownloaded = False
        self.startingDownload = False
        self.lastDownloadFailed = False
        self.pendingManualDL = False
        self.downloadedTime = None
        self.pendingReason = ""
        self.entry = entry
        self.dlFactory = DownloaderFactory(self)
        self.expired = False
        self.keep = False

        self.iconCache = IconCache(self)
        
        # linkNumber is a hack to make sure that scraped items at the
        # top of a page show up before scraped items at the bottom of
        # a page. 0 is the topmost, 1 is the next, and so on
        self.linkNumber = linkNumber
        self.creationTime = datetime.now()
        DDBObject.__init__(self)

    # Unfortunately, our database does not scale well with many views,
    # so we have this hack to make sure that unwatched and available
    # get updated when an item changes
    def endChange(self):
        DDBObject.endChange(self)
        self.getFeed().updateUandA()

    #
    # Returns True iff this item has never been viewed in the interface
    # Note the difference between "viewed" and seen
    def getViewed(self):
        return self.creationTime <= self.getFeed().lastViewed

    ##
    # Returns the first video enclosure in the item
    def getFirstVideoEnclosure(self):
        first = None
        self.beginRead()
        try:
            try:
                for enclosure in self.entry.enclosures:
                    if isVideoEnclosure(enclosure):
                        first = enclosure
                        break
            except:
                pass
        finally:
            self.endRead()
        return first

    ##
    # Returns the URL associated with the first enclosure in the item
    def getURL(self):
        ret = ''
        self.beginRead()
        try:
            try:
                ret = self.getFirstVideoEnclosure().url
            except:
                pass
        finally:
            self.endRead()
        return ret
    ##
    # Returns the feed this item came from
    def getFeed(self):
        return self.dd.getObjectByID(self.feed_id)

    ##
    # Moves this item to another feed.
    def setFeed(self, feed_id):
        self.feed_id = feed_id
        self.endChange()

    ##
    # Returns the number of videos associated with this item
    def getAvailableVideos(self):
        ret = 0
        self.beginRead()
        try:
            ret = len(self.entry.enclosures)
        finally:
            self.endRead()
        return ret

    ##
    # Marks this item as expired
    def expire(self):
        self.beginRead()
        try:
            self.stopDownload()
            # FIXME: should expired items be marked as "seen?"
            # self.markItemSeen()
            self.expired = True
        finally:
            self.endRead()        
        self.beginChange()
        self.endChange()

    ##
    # Returns string with days or hours until this gets deleted
    def getExpirationTime(self):
        ret = "???"
        self.beginRead()
        ufeed = self.getFeed()
        ufeed.beginRead()
        try:
            if ufeed.expire == 'never' or (ufeed.expire == 'system'
                    and config.get(prefs.EXPIRE_AFTER_X_DAYS) <= 0):
                ret = "never"
            else:
                if ufeed.expire == "feed":
                    expireTime = ufeed.expireTime
                elif ufeed.expire == "system":
                    expireTime = timedelta(days=config.get(prefs.EXPIRE_AFTER_X_DAYS))
                
                exp = expireTime - (datetime.now() - self.getDownloadedTime())
                if exp.days > 0:
                    ret = "%d days" % exp.days
                elif exp.seconds > 3600:
                    ret = "%d hours" % (ceil(exp.seconds/3600.0))
                else:
                    ret = "%d min." % (ceil(exp.seconds/60.0))
        finally:
            ufeed.endRead()
            self.endRead()
        return ret

    def getKeep(self):
        self.beginRead()
        ret = self.keep
        self.endRead()
        return ret

    def setKeep(self,val):
        self.beginRead()
        self.keep = val
        self.endRead()
        self.beginChange()
        self.endChange()

    ##
    # returns true iff video has been seen
    # Note the difference between "viewed" and "seen"
    def getSeen(self):
        self.beginRead()
        ret = self.seen
        self.endRead()
        return ret

    ##
    # Marks the item as seen
    def markItemSeen(self):
        self.beginChange()
        try:
            self.seen = True
        finally:
            self.endChange()

    ##
    # Returns a list of downloaders associated with this object
    def getDownloaders(self):
        self.beginRead()
        ret = self.downloaders
        self.endRead()
        return ret

    def getRSSID(self):
        self.beginRead()
        try:
            ret = self.entry["id"]
        finally:
            self.endRead()
        return ret

    def setAutoDownloaded(self,autodl = True):
        self.beginRead()
        self.autoDownloaded = autodl
        self.endRead()

    def getPendingReason(self):
        ret = ""
        self.beginRead()
        ret = self.pendingReason
        self.endRead()
        return ret

    ##
    # Returns true iff item was auto downloaded
    def getAutoDownloaded(self):
        self.beginRead()
        ret = self.autoDownloaded
        self.endRead()
        return ret

    ##
    # Returns the linkNumber
    def getLinkNumber(self):
        self.beginRead()
        try:
            ret = self.linkNumber
        finally:
            self.endRead()
        return ret

    def download(self,autodl=False):
        eventloop.addIdle(lambda : self.actualDownload(autodl), "Spawning Download %s" % self.getURL())

    ##
    # Starts downloading the item
    def actualDownload(self,autodl=False):
        spawn = True
        self.beginRead()
        try:
            # FIXME: For locking reasons, downloaders don't always
            #        call beginChange() and endChange(), so we have to
            #        recompute this filter
            defaultDatabase.recomputeFilter(self.manualDownloads)
            if ((not autodl) and 
                self.manualDownloads.len() >= config.get(prefs.MAX_MANUAL_DOWNLOADS)):
                self.pendingManualDL = True
                self.pendingReason = "Too many manual downloads"
                spawn = False
                self.expired = False
            else:
                #Don't spawn two downloaders
                if self.startingDownload:
                    spawn = False
                else:
                    self.setAutoDownloaded(autodl)
                    self.expired = False
                    self.keep = False
                    self.pendingManualDL = False
                    self.lastDownloadFailed = False
                    downloadURLs = map(lambda x:x.getURL(),self.downloaders)
                    self.startingDownload = True
            try:
                enclosures = self.entry["enclosures"]
            except:
                enclosures = []
        finally:
            self.endRead()
        self.beginChange()
        self.endChange()

        if not spawn:
            return

        try:
            justStartedDownloaders = set()
            for enclosure in enclosures:
                try:
                    if enclosure["url"] not in downloadURLs:
                        dler = self.dlFactory.getDownloader(enclosure["url"])
                        if dler != None:
                            self.beginRead()
                            try:
                                self.downloaders.append(dler)
                                downloadURLs.append(dler.getURL())
                                justStartedDownloaders.add(dler.getURL())
                            finally:
                                self.endRead()
                        else:
                            self.beginRead()
                            try:
                                self.lastDownloadFailed = True
                            finally:
                                self.endRead()
                    elif enclosure['url'] not in justStartedDownloaders:
                        for dler in self.downloaders:
                            if dler.getURL() == enclosure['url']:
                                dler.start()
                except KeyError:
                    pass
        except KeyError:
            pass
        self.beginRead()
        try:
            self.startingDownload = False
        finally:
            self.endRead()
        self.beginChange()
        self.endChange()


    ##
    # Returns a link to the thumbnail of the video
    def getThumbnailURL(self):
        ret = None
        self.beginRead()
        try:
            if self.entry.has_key('enclosures'):
                try:
                    self.entry.enclosures
                except AttributeError:
                    print "self.entry.enclosures doesn't work"
                    print "self.entry['enclosures'] is: "
                    print self.entry['enclosures']
                for enc in self.entry.enclosures:
                    if enc.has_key('thumbnail') and enc['thumbnail'].has_key('url'):
                        ret = enc["thumbnail"]["url"]
                        break
            if (ret is None and self.entry.has_key('thumbnail') and
                self.entry['thumbnail'].has_key('url')):
                ret =  self.entry["thumbnail"]["url"]
        finally:
            self.endRead()
        return ret

    def getThumbnail (self):
        self.beginRead()
        try:
            if self.iconCache.isValid():
                basename = os.path.basename(self.iconCache.getFilename())
                return resource.iconCacheUrl(basename)
            else:
                return "resource:images/thumb.png"
        finally:
            self.endRead()
    ##
    # returns the title of the item
    def getTitle(self):
        try:
            return self.entry.title
        except:
            try:
                enclosure = self.getFirstVideoEnclosure()
                return enclosure["url"]
            except:
                return ""

    ##
    # Returns valid XHTML containing a description of the video
    def getDescription(self):
        self.beginRead()
        try:
            enclosure = self.getFirstVideoEnclosure()
            ret = xhtmlify('<span>'+unescape(enclosure["text"])+'</span>')
        except:
            try:
                ret = xhtmlify('<span>'+unescape(self.entry.description)+'</span>')
            except:
                ret = '<span />'
        self.endRead()
        return ret

    def looksLikeTorrent(self):
        """Returns true if we think this item is a torrent.  (For items that
        haven't been downloaded this uses the file extension which isn't
        totally reliable).
        """

        if len(self.downloaders) > 0:
            return self.downloaders[0].getType() == 'bittorrent'
        else:
            return self.getURL().endswith('.torrent')

    ##
    # Returns formatted XHTML with release date, duration, format, and size
    def getDetails(self):
        details = []
        reldate = self.getReleaseDate()
        duration = self.getDuration()
        format = self.getFormat()
        size = self.getSizeForDisplay()
        if len(reldate) > 0:
            details.append('<span class="details-date">%s</span>' % escape(reldate))
        if len(duration) > 0:
            details.append('<span class="details-duration">%s</span>' % escape(duration))
        if len(format) > 0:
            details.append('<span class="details-format">%s</span>' % escape(format))
        if len(size) > 0:
            details.append('<span class="details-size">%s</span>' % escape(size))
        if self.looksLikeTorrent():
            details.append('<span class="details-torrent" il8n:translate="">TORRENT</span>')
        out = ' - '.join(details)
        return '<div class="main-video-details-under">%s</div>' % out

    ##
    # Stops downloading the item
    def stopDownload(self):
        for dler in self.downloaders:
            dler.remove()
        self.beginRead()
        try:
            self.downloaders = []
            self.keep = False
            self.pendingManualDL = False
        finally:
            self.endRead()

    ##
    # returns status of the download in plain text
    def getState(self):
        ufeed = self.getFeed()
        self.beginRead()
        ufeed.beginRead()
        try:
            state = self.getStateNoAuto()
            lastPubDate = self.getPubDateParsed()
            if ((state == "stopped") and 
                ufeed.isAutoDownloadable() and 
                (ufeed.getEverything or 
                 (lastPubDate >= ufeed.startfrom and
                  lastPubDate != datetime.max))):
                state = "autopending"
        finally:
            ufeed.endRead()
            self.endRead()
            
        return state
    

    ##
    # returns the state of the download, without checking automatic dl
    # eligibility
    def getStateNoAuto(self):
        self.beginRead()
        try:
            if self.expired:
                state = "expired"
            elif self.startingDownload:
                state = "downloading"
            elif self.keep:
                state = "saved"
            elif self.pendingManualDL:
                state = "manualpending"
            elif len(self.downloaders) == 0:
                if self.lastDownloadFailed:
                    state = "failed"
                else:
                    state = "stopped"
            else:
                state = "finished"
                for dler in self.downloaders:
                    newState = dler.getState()
                    if newState != "finished":
                        state = newState
                    if state == "failed":
                        break
            if (state == "finished" or state=="uploading") and self.seen:
                state = "watched"
        finally:
            self.endRead()
        return state

    def getFailureReason(self):
        ret = ""
        self.beginRead()
        try:
            if self.lastDownloadFailed:
                ret = "Could not connect to server"
            else:
                for dler in self.downloaders:
                    if dler.getState() == "failed":
                        ret = dler.getReasonFailed()
                        break
        finally:
            self.endRead()
        return ret
    
    ##
    # Returns the size of the item to be displayed. If the item has a corresponding
    # downloaded enclosure we use the pysical size of the file, otherwise we use
    # the RSS enclosure tag values.
    def getSizeForDisplay(self):
        fname = self.getFilename()
        if fname != "" and os.path.exists(fname):
            size = os.stat(fname)[6]
            return self.sizeFormattedForDisplay(size)
        else:
            return self.getEnclosuresSize()
    
    ##
    # Returns the total size of all enclosures in bytes
    def getEnclosuresSize(self):
        size = 0
        try:
            if self.entry.has_key('enclosures'):
                enclosures = self.entry['enclosures']
                for enclosure in enclosures:
                    try:
                        size += int(enclosure['length'])
                    except:
                        pass
        except:
            pass
        return self.sizeFormattedForDisplay(size)

    ##
    # returns status of the download in plain text
    def getTotalSize(self):
        size = 0
        for dler in self.downloaders:
            try:
                size += dler.getTotalSize()
            except:
                pass
        if size == 0:
            return ""
        return self.sizeFormattedForDisplay(size)

    ##
    # returns status of the download in plain text
    def getCurrentSize(self):
        size = 0
        for dler in self.downloaders:
            size += dler.getCurrentSize()
        if size == 0:
            return ""
        return self.sizeFormattedForDisplay(size)

    ##
    # Returns a byte size formatted for display
    def sizeFormattedForDisplay(self, bytes, emptyForZero=True):
        if bytes > (1 << 30):
            return "%1.1fGB" % (bytes / (1024.0 * 1024.0 * 1024.0))
        elif bytes > (1 << 20):
            return "%1.1fMB" % (bytes / (1024.0 * 1024.0))
        elif bytes > (1 << 10):
            return "%1.1fKB" % (bytes / 1024.0)
        elif bytes > 1:
            return "%0.0fB" % bytes
        else:
            if emptyForZero:
                return ""
            else:
                return "n/a"

    ##
    # Returns the download progress in absolute percentage [0.0 - 100.0].
    def downloadProgress(self):
        progress = 0
        self.beginRead()
        try:
            size = 0
            dled = 0
            for dler in self.downloaders:
                try:
                    size += dler.getTotalSize()
                    dled += dler.getCurrentSize()
                except:
                    pass
            if size > 0:
                progress = (100.0*dled) / size
        finally:
            self.endRead()
        return progress

    ##
    # Returns the width of the progress bar corresponding to the current
    # download progress. This doesn't really belong here and even forces
    # to use a hardcoded constant, but the templating system doesn't 
    # really leave any other choice.
    def downloadProgressWidth(self):
        fullWidth = 92  # width of resource:channelview-progressbar-bg.png - 2
        progress = self.downloadProgress() / 100.0
        if progress == 0:
            return 0
        return int(progress * fullWidth)

    ##
    # Returns string containing three digit percent finished
    # "000" through "100".
    def threeDigitPercentDone(self):
        return '%03d' % int(self.downloadProgress())

    ##
    # Returns string with estimate time until download completes
    def downloadETA(self):
        secs = 0
        for dler in self.downloaders:
            secs += dler.getETA()
        if secs == 0:
            return 'starting up...'
        elif (secs < 120):
            return '%1.0f secs left - ' % secs
        elif (secs < 6000):
            return '%1.0f mins left - ' % ceil(secs/60.0)
        else:
            return '%1.1f hours left - ' % ceil(secs/3600.0)

    ##
    # Returns the download rate
    def downloadRate(self):
        rate = 0
        unit = "k/s"
        if len(self.downloaders) > 0:
            for dler in self.downloaders:
                rate = dler.getRate()
            rate /= len(self.downloaders)

        rate /= 1024
        if rate > 1000:
            rate /= 1024
            unit = "m/s"
        if rate > 1000:
            rate /= 1024
            unit = "g/s"
            
        return "%d%s" % (rate, unit)

    ##
    # Returns the published date of the item
    def getPubDate(self):
        self.beginRead()
        try:
            try:
                ret = datetime(*self.entry.modified_parsed[0:7]).strftime("%b %d %Y").decode(_charset)
            except:
                ret = ""
        finally:
            self.endRead()
        return ret
    
    ##
    # Returns the published date of the item as a datetime object
    def getPubDateParsed(self):
        self.beginRead()
        try:
            try:
                ret = datetime(*self.entry.modified_parsed[0:7])
            except:
                ret = datetime.max # Is this reasonable? It should
                                   # avoid type issues for now, if
                                   # nothing else
        finally:
            self.endRead()
        return ret

    ##
    # returns the date this video was released or when it was published
    def getReleaseDate(self):
        try:
            return self.releaseDate
        except:
            try:
                self.releaseDate = datetime(*self.getFirstVideoEnclosure().modified_parsed[0:7]).strftime("%b %d %Y").decode(_charset)
                return self.releaseDate
            except:
                try:
                    self.releaseDate = datetime(*self.entry.modified_parsed[0:7]).strftime("%b %d %Y").decode(_charset)
                    return self.releaseDate
                except:
                    self.releaseDate = ""
                    return self.releaseDate
            

    ##
    # returns the date this video was released or when it was published
    def getReleaseDateObj(self):
        if hasattr(self,'releaseDateObj'):
            return self.releaseDateObj
        self.beginRead()
        try:
            try:
                self.releaseDateObj = datetime(*self.getFirstVideoEnclosure().modified_parsed[0:7])
            except:
                try:
                    self.releaseDateObj = datetime(*self.entry.modified_parsed[0:7])
                except:
                    self.releaseDateObj = datetime.min
        finally:
            self.endRead()
        return self.releaseDateObj

    ##
    # returns string with the play length of the video
    def getDuration(self, emptyIfZero=True):
        secs = 0
        #FIXME get this from VideoInfo
        if secs == 0:
            if emptyIfZero:
                return ""
            else:
                return "n/a"
        if (secs < 120):
            return '%1.0f secs' % secs
        elif (secs < 6000):
            return '%1.0f mins' % ceil(secs/60.0)
        else:
            return '%1.1f hours' % ceil(secs/3600.0)

    ##
    # returns string with the format of the video
    KNOWN_MIME_TYPES = ('audio', 'video')
    KNOWN_MIME_SUBTYPES = ('mov', 'wmv', 'mp4', 'mp3', 'mpg', 'mpeg', 'avi')
    def getFormat(self, emptyForUnknown=True):
        try:
            enclosure = self.entry['enclosures'][0]
            if enclosure.has_key('type') and len(enclosure['type']) > 0:
                type, subtype = enclosure['type'].split('/')
                if type.lower() in self.KNOWN_MIME_TYPES:
                    return subtype.split(';')[0].upper()
            else:
                extension = enclosure['url'].split('.')[-1].lower()
                if extension in self.KNOWN_MIME_SUBTYPES:
                    return extension.upper()
        except:
            pass
        if emptyForUnknown:
            return ""
        else:
            return "n/a"

    ##
    # return keyword tags associated with the video separated by commas
    def getTags(self):
        self.beginRead()
        try:
            try:
                ret = self.entry.categories.join(", ")
            except:
                ret = ""
        finally:
            self.endRead()
        return ret

    ##
    # return the license associated with the video
    def getLicence(self):
        self.beginRead()
        try:
            try:
                ret = self.entry.license
            except:
                try:
                    ret = self.getFeed().getLicense()
                except:
                    ret = ""
        finally:
            self.endRead()
        return ret

    ##
    # return the people associated with the video, separated by commas
    def getPeople(self):
        ret = []
        self.beginRead()
        try:
            try:
                for role in self.getFirstVideoEnclosure().roles:
                    for person in self.getFirstVideoEnclosure().roles[role]:
                        ret.append(person)
                for role in self.entry.roles:
                    for person in self.entry.roles[role]:
                        ret.append(person)
            except:
                pass
        finally:
            self.endRead()
        return ', '.join(ret)

    ##
    # returns the URL of the webpage associated with the item
    def getLink(self):
        self.beginRead()
        try:
            try:
                ret = self.entry.link
            except:
                ret = ""
        finally:
            self.endRead()
        return ret

    ##
    # returns the URL of the payment page associated with the item
    def getPaymentLink(self):
        self.beginRead()
        try:
            try:
                ret = self.getFirstVideoEnclosure().payment_url
            except:
                try:
                    ret = self.entry.payment_url
                except:
                    ret = ""
        finally:
            self.endRead()
        return ret

    ##
    # returns a snippet of HTML containing a link to the payment page
    # HTML has already been sanitized by feedparser
    def getPaymentHTML(self):
        self.beginRead()
        try:
            try:
                ret = self.getFirstVideoEnclosure().payment_html
            except:
                try:
                    ret = self.entry.payment_html
                except:
                    ret = ""
        finally:
            self.endRead()
        # feedparser returns escaped CDATA so we either have to change its
        # behavior when it parses dtv:paymentlink elements, or simply unescape
        # here...
        return '<span>' + unescape(ret) + '</span>'

    ##
    # Updates an item with new data
    #
    # @param entry a dict object containing the new data
    def update(self, entry):
        self.beginChange()
        try:
            self.entry = entry
            self.iconCache.requestUpdate()
        finally:
            self.endChange()

    ##
    # marks the item as having been downloaded now
    def setDownloadedTime(self):
        self.beginRead()
        try:
            self.downloadedTime = datetime.now()

            # Hack to immediately "save" items in feeds set to never expire
            self.keep = (self.getFeed().expire == "never")
        finally:
            self.endRead()

    ##
    # gets the time the video was downloaded
    # Only valid if the state of this item is "finished"
    def getDownloadedTime(self):
        self.beginRead()
        try:
            if self.downloadedTime is None:
                return datetime.min
            else:
                return self.downloadedTime
        finally:
            self.endRead()

    ##
    # gets the time the video started downloading
    def getDLStartTime(self):
        self.beginRead()
        try:
            try:
                ret = self.DLStartTime
            except:
                ret = None
        finally:
            self.endRead()
        return ret

    ##
    # Returns the filename of the first downloaded video or the empty string
    # NOTE: this will always return the absolute path to the file.
    def getFilename(self):
        ret = ""
        self.beginRead()
        try:
            try:
                ret = self.downloaders[0].getFilename()
            except:
                pass
        finally:
            self.endRead()
        return ret

    ##
    # Returns a list with the filenames of all of the videos in the item
    def getFilenames(self):
        ret = []
        self.beginRead()
        try:
            try:
                for dl in self.downloaders:
                    ret.append(dl.getFilename())
            except:
                pass
        finally:
            self.endRead()
        return ret

    def getRSSEntry(self):
        self.beginRead()
        try:
            ret = self.entry
        finally:
            self.endRead()
        return ret

    def remove(self):
        for dler in self.downloaders:
            dler.remove()
        DDBObject.remove(self)

    def reconnectDownloaders(self):
        changed = False
        for enclosure in self.entry["enclosures"]:
            url = enclosure["url"]
            downloader = self.dlFactory.getDownloader(url, create=False)
            if downloader:
                self.downloaders.append(downloader)
                downloader.addItem(self)
                changed = True
        if changed:
            self.endChange()

    ##
    # Called by pickle during serialization
    def onRestore(self):
        self.startingDownload = False
        self.dlFactory = DownloaderFactory(self)
        if (self.iconCache == None):
            self.iconCache = IconCache (self)
        else:
            self.iconCache.dbItem = self
            self.iconCache.requestUpdate()
        self.downloaders = []

    def __str__(self):
        return "Item - %s" % self.getTitle()

def reconnectDownloaders():
    for item in views.items:
        item.reconnectDownloaders()

def getEntryForFile(filename):
    return FeedParserDict({'title':os.path.basename(filename),
            'enclosures':[{'url': 'file://%s' % filename}]})

##
# An Item that exists as a local file
class FileItem(Item):

    def __init__(self,feed_id,filename):
        filename = os.path.abspath(filename)
        self.filename = filename
        self.deleted = False
        Item.__init__(self, feed_id, getEntryForFile(filename))

    def getState(self):
        if self.deleted:
            return "deleted"
        elif self.getSeen():
            return "saved"
        else:
            return "finished"

    def expire(self):
        title = _("Removing %s") % (os.path.basename(self.filename))
        description = _("Would you like to delete this file or just remove "
                "its entry from My Collection?")
        d = dialogs.ThreeChoiceDialog(title, description,
                dialogs.BUTTON_REMOVE_ENTRY, dialogs.BUTTON_DELETE_FILE,
                dialogs.BUTTON_CANCEL)
        def callback(dialog):
            if dialog.choice == dialogs.BUTTON_DELETE_FILE:
                try:
                    os.remove(self.filename)
                except:
                    print "WARNING: Error deleting %s" % self.filename
                    traceback.print_exc()
                self.remove()
            elif dialog.choice == dialogs.BUTTON_REMOVE_ENTRY:
                self.beginChange()
                try:
                    self.deleted = True
                finally:
                    self.endChange()

        d.run(callback)

    def getDownloadedTime(self):
        self.beginRead()
        try:
            try:
                time = datetime.fromtimestamp(os.getctime(self.filename))
            except:
                return datetime.min
        finally:
            self.endRead()

    def getFilename(self):
        ret = ''
        try:
            ret = self.filename
        except:
            pass
        return ret

    def migrate(self, newDir):
        self.beginChange()
        try:
            if os.path.exists(self.filename):
                newFilename = os.path.join(newDir, os.path.basename(self.filename))
                try:
                    shutil.move(self.filename, newFilename)
                except IOError, e:
                    print "WARNING: Error moving %s to %s (%s)" % (self.filename,
                            newFilename, e)
                else:
                    self.filename = newFilename
        finally:
             self.endChange()

    def getFilenames(self):
        ret = []
        try:
            ret = [self.filename]
        except:
            pass
        return ret


def isVideoEnclosure(enclosure):
    """
    Pass an enclosure dictionary to this method and it will return a boolean
    saying if the enclosure is a video or not.
    """
    hasVideoType = (enclosure.has_key('type') and
        (enclosure['type'].startswith('video/') or
         enclosure['type'].startswith('audio/') or
         enclosure['type'] == "application/ogg" or
         enclosure['type'] == "application/x-annodex" or
         enclosure['type'] == "application/x-bittorrent"))
    hasVideoExtension = (enclosure.has_key('url') and
        ((len(enclosure['url']) > 4 and
          enclosure['url'][-4:].lower() in ['.mov','.wmv','.mp4', '.m4v',
                                      '.mp3','.ogg','.anx','.mpg','.avi']) or
         (len(enclosure['url']) > 8 and
          enclosure['url'][-8].lower() == '.torrent') or
         (len(enclosure['url']) > 5 and
          enclosure['url'][-5].lower() == '.mpeg')))
    return hasVideoType or hasVideoExtension
