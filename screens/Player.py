# -*- coding: utf-8 -*-
"""
Custom player screen with OSD, resume, seek, pause, and proxy support.
"""
import sys
import time
import re
from Screens.Screen import Screen
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.ServiceEventTracker import ServiceEventTracker
from enigma import eTimer, eServiceReference, iPlayableService
from ..utils.base import log
from ..utils.player import build_remote_play_candidates, start_proxy
from ..utils.library import save_position
from ..utils.state import get_config

# Global position tracker (copied from ArabicPlayer)
_GLOBAL_POS_TIMER = None
_GLOBAL_POS_SESSION = None
_GLOBAL_POS_ITEM = ""
_GLOBAL_PLAY_START_WALL = 0.0
_GLOBAL_PLAY_START_POS = 0
_GLOBAL_LAST_SEEK_TARGET = -1

def _start_pos_tracker(session, item_url, start_pos=0):
    global _GLOBAL_POS_TIMER, _GLOBAL_POS_SESSION, _GLOBAL_POS_ITEM
    global _GLOBAL_PLAY_START_WALL, _GLOBAL_PLAY_START_POS, _GLOBAL_LAST_SEEK_TARGET
    _GLOBAL_LAST_SEEK_TARGET = -1
    _GLOBAL_POS_SESSION = session
    _GLOBAL_POS_ITEM = item_url or ""
    _GLOBAL_PLAY_START_WALL = time.time()
    _GLOBAL_PLAY_START_POS = int(start_pos or 0)
    if _GLOBAL_POS_TIMER is None:
        _GLOBAL_POS_TIMER = eTimer()
        _GLOBAL_POS_TIMER.callback.append(_global_pos_tick)
    try:
        _GLOBAL_POS_TIMER.stop()
    except Exception:
        pass
    if _GLOBAL_POS_ITEM:
        _GLOBAL_POS_TIMER.start(20000, False)

def _global_pos_tick():
    global _GLOBAL_POS_ITEM, _GLOBAL_PLAY_START_WALL, _GLOBAL_PLAY_START_POS
    if not _GLOBAL_POS_ITEM or not _GLOBAL_PLAY_START_WALL:
        return
    try:
        elapsed = time.time() - _GLOBAL_PLAY_START_WALL
        secs = int(_GLOBAL_PLAY_START_POS + elapsed)
        if secs < 5:
            return
        save_position(_GLOBAL_POS_ITEM, secs)
    except Exception as e:
        log("Pos tracker error: {}".format(e))

def _stop_pos_tracker():
    global _GLOBAL_POS_ITEM
    _GLOBAL_POS_ITEM = ""
    try:
        if _GLOBAL_POS_TIMER:
            _GLOBAL_POS_TIMER.stop()
    except Exception:
        pass

def _copy_service_ref(sref):
    if not sref:
        return None
    try:
        return eServiceReference(sref.toString())
    except Exception:
        try:
            return eServiceReference(str(sref.toString()))
        except Exception:
            return sref

def _capture_previous_service(session):
    try:
        return _copy_service_ref(session.nav.getCurrentlyPlayingServiceReference())
    except Exception as e:
        log("Capture previous service failed: {}".format(e))
        return None

def _restore_previous_service(session, previous_service):
    if not previous_service:
        return
    try:
        session.nav.stopService()
    except Exception:
        pass
    try:
        session.nav.playService(previous_service)
        log("Previous service restored")
    except Exception as e:
        log("Restore previous service failed: {}".format(e))

class MultiMoviePlayer(Screen):
    skin = """
    <screen name="MultiMoviePlayer" position="0,0" size="1920,1080" flags="wfNoBorder" backgroundColor="transparent">
        <widget name="osd_shadow"   position="148,856" size="1624,230" backgroundColor="#000000" zPosition="9" />
        <widget name="overlay_bg"   position="160,860" size="1600,210" backgroundColor="#0A0E14" zPosition="10" />
        <widget name="osd_topline"  position="160,860" size="1600,3" backgroundColor="#00E5FF" zPosition="11" />
        <widget name="osd_titlebar" position="160,860" size="1600,52" backgroundColor="#0D1520" zPosition="11" />
        <widget name="osd_title"    position="180,868" size="1180,38" font="Regular;30" foregroundColor="#00E5FF" transparent="1" zPosition="12" halign="left" />
        <widget name="osd_durtext"  position="1380,868" size="360,38" font="Regular;26" foregroundColor="#8B949E" transparent="1" zPosition="12" halign="right" />
        <widget name="prog_bar"     position="160,906" size="1600,30" font="Regular;22" foregroundColor="#00B4D8" transparent="1" zPosition="12" halign="left" />
        <widget name="osd_elapsed"  position="180,938" size="320,44" font="Regular;36" foregroundColor="#FFD740" transparent="1" zPosition="12" />
        <widget name="status"       position="640,938" size="640,44" font="Regular;36" foregroundColor="#39D98A" transparent="1" zPosition="12" halign="center" />
        <widget name="osd_hints"    position="1220,938" size="520,44" font="Regular;26" foregroundColor="#8B949E" transparent="1" zPosition="12" halign="right" />
        <widget name="osd_divider"  position="160,982" size="1600,2" backgroundColor="#1C2333" zPosition="11" />
        <widget name="osd_keybar"   position="160,984" size="1600,46" backgroundColor="#0D1520" zPosition="11" />
        <widget name="osd_keys"     position="180,992" size="1560,34" font="Regular;24" foregroundColor="#484F58" transparent="1" zPosition="12" halign="center" />
        <widget name="osd_botline"  position="160,1027" size="1600,3" backgroundColor="#0A2040" zPosition="11" />
    </screen>
    """

    def __init__(self, session, title, candidates, previous_service=None, resume_pos=0, item_url=""):
        Screen.__init__(self, session)
        self["overlay_bg"]   = Label("")
        self["status"]       = Label("جاري التشغيل...")
        self["osd_shadow"]   = Label("")
        self["osd_titlebar"] = Label("")
        self["osd_title"]    = Label("")
        self["osd_durtext"]  = Label("")
        self["osd_topline"]  = Label("")
        self["prog_bar"]     = Label("")
        self["osd_elapsed"]  = Label("")
        self["osd_hints"]    = Label("")
        self["osd_divider"]  = Label("")
        self["osd_keybar"]   = Label("")
        self["osd_keys"]     = Label("")
        self["osd_botline"]  = Label("")
        _raw = (title or "").strip()
        _qtag_m = re.search(r'\s*(\[\d+p\])\s*$', _raw)
        _qtag = _qtag_m.group(1) if _qtag_m else ""
        _bare = _raw[:_qtag_m.start()].strip() if _qtag_m else _raw
        if len(_bare) > 34:
            _bare = _bare[:32].rstrip() + u"\u2026"
        self.title = (_bare + " " + _qtag).strip() if _qtag else _bare
        self.candidates = candidates or []
        self.previous_service = _copy_service_ref(previous_service)
        self.sref = None
        self._play_confirmed = False
        self._candidate_idx = -1
        self._candidate_start_ts = 0
        self._candidate_uses_proxy = False
        self._candidate_label = ""
        self._handoff = False
        self._restored_previous = False
        self._resume_pos = int(resume_pos or 0)
        self._item_url = item_url or ""
        self._seek_timer = eTimer()
        self._seek_timer.callback.append(self.__doSeek)
        self._seek_retry_count = 0
        self._seek_verify_timer = eTimer()
        self._seek_verify_timer.callback.append(self.__verifySeek)
        self._hide_timer = eTimer()
        self._hide_timer.callback.append(self.__hideOSD)
        self._osd_update_timer = eTimer()
        self._osd_update_timer.callback.append(self.__updateOSD)
        self._osd_visible = False
        self._total_secs = 0
        self._osd_auto_hide_secs = 4
        self._paused = False
        self._paused_elapsed = 0
        self._force_confirmation_timer = eTimer()
        self._force_confirmation_timer.callback.append(self.__forceConfirm)
        self["actions"] = ActionMap(
            ["OkCancelActions", "MediaPlayerActions", "InfobarSeekActions", "DirectionActions", "ColorActions"],
            {
                "cancel":           self.__onExit,
                "stop":             self.__onExit,
                "ok":               self.__togglePause,
                "playpauseService": self.__togglePause,
                "right":            lambda: self.__seek(+10),
                "left":             lambda: self.__seek(-10),
                "seekFwd":          lambda: self.__seek(+60),
                "seekBack":         lambda: self.__seek(-60),
                "green":            self.__onRestart,
            },
            -1
        )
        self._retry_timer = eTimer()
        self._retry_timer.callback.append(self.__onTimeout)
        eventmap = {
            iPlayableService.evTuneFailed: self.__onFailed,
            iPlayableService.evEOF: self.__onFailed,
        }
        ev_video = getattr(iPlayableService, "evVideoSizeChanged", None)
        if ev_video is not None:
            eventmap[ev_video] = self.__onConfirmed
        self._events = ServiceEventTracker(screen=self, eventmap=eventmap)
        self.onLayoutFinish.append(self.__initOSD)
        self.onLayoutFinish.append(self.__playNext)
        self.onClose.append(self.__stop)

    _OSD_WIDGETS = [
        "osd_shadow","overlay_bg","osd_topline","osd_botline",
        "osd_titlebar","osd_title","osd_durtext",
        "prog_bar","osd_elapsed",
        "status","osd_hints","osd_divider",
        "osd_keybar","osd_keys",
    ]

    def __initOSD(self):
        for w in self._OSD_WIDGETS:
            try: self[w].hide()
            except: pass

    def __hideOSD(self):
        self._osd_visible = False
        try: self._osd_update_timer.stop()
        except: pass
        for w in self._OSD_WIDGETS:
            try: self[w].hide()
            except: pass

    def __showOSD(self, auto_hide=True):
        self._osd_visible = True
        for w in self._OSD_WIDGETS:
            try: self[w].show()
            except: pass
        self.__updateOSD()
        try:
            self._osd_update_timer.start(1000, False)
        except: pass
        if auto_hide:
            try:
                self._hide_timer.stop()
                self._hide_timer.start(self._osd_auto_hide_secs * 1000, True)
            except: pass

    def __updateOSD(self):
        if not self._osd_visible:
            try: self._osd_update_timer.stop()
            except: pass
            return
        try:
            if self._paused:
                elapsed = self._paused_elapsed
            else:
                wall = _GLOBAL_PLAY_START_WALL
                base = _GLOBAL_PLAY_START_POS
                if wall and base >= 0:
                    elapsed = max(0, int((time.time() - wall) + base))
                else:
                    elapsed = 0
            he = elapsed // 3600; me = (elapsed % 3600) // 60; se = elapsed % 60
            self["osd_elapsed"].setText("{:02d}:{:02d}:{:02d}".format(he, me, se))
            total = self._total_secs
            if not total:
                try:
                    svc = self.session.nav.getCurrentService()
                    seek = svc and svc.seek()
                    if seek:
                        r = seek.getLength()
                        if r and r[0] == 0 and r[1] > 0:
                            total = r[1] // 90000
                            self._total_secs = total
                except: pass
            if total > 0:
                rem = max(0, total - elapsed)
                pct = min(1.0, float(elapsed) / float(total))
                hr = rem // 3600; mr = (rem % 3600) // 60; sr = rem % 60
                ht = total // 3600; mt = (total % 3600) // 60; st = total % 60
                self["osd_durtext"].setText("-{:02d}:{:02d}:{:02d}  {:02d}:{:02d}:{:02d}".format(hr, mr, sr, ht, mt, st))
                BAR_W = 96
                filled = max(0, min(BAR_W, int(pct * BAR_W)))
                bar = u"█" * filled + u"░" * (BAR_W - filled)
                self["prog_bar"].setText(u"{} {:.1f}%".format(bar, pct * 100))
            else:
                self["osd_durtext"].setText("")
                self["prog_bar"].setText("")
            self["osd_keys"].setText("OK=Pause   << -10s   +10s >>   <<< -60s   +60s >>>   Green=إعادة+استئناف   Stop=حفظ&خروج")
        except Exception as e:
            log("updateOSD error: {}".format(e))

    def __forceConfirm(self):
        if not self._play_confirmed:
            log("Force confirm")
            self.__onConfirmed()

    def __playNext(self):
        global _PROXY_LAST_HIT, _PROXY_LAST_BYTES
        self._candidate_idx += 1
        if self._candidate_idx >= len(self.candidates):
            self["status"].setText("تعذر تشغيل الرابط على كل المحاولات")
            return
        p_type, svc_url, label, uses_proxy = self.candidates[self._candidate_idx]
        self._play_confirmed = False
        self._candidate_start_ts = time.time()
        self._candidate_uses_proxy = uses_proxy
        self._candidate_label = label
        if uses_proxy:
            _PROXY_LAST_HIT = 0
            _PROXY_LAST_BYTES = 0
        self.sref = eServiceReference(p_type, 0, svc_url)
        if sys.version_info[0] == 3:
            self.sref.setName(str(self.title))
        else:
            self.sref.setName(self.title.encode("utf-8", "ignore"))
        self["status"].setText("جاري التشغيل... {}".format(label))
        log("Play attempt: {}".format(label))
        try:
            self.session.nav.stopService()
        except: pass
        try:
            self.session.nav.playService(self.sref)
            self._retry_timer.start(12000, True)
            self._force_confirmation_timer.start(3000, True)
        except Exception as e:
            log("Player fallback error: {}".format(e))
            self.__playNext()

    def __onConfirmed(self):
        if self._play_confirmed:
            return
        self._play_confirmed = True
        try:
            self._retry_timer.stop()
            self._force_confirmation_timer.stop()
        except: pass
        log("Play confirmed: {}".format(self._candidate_label))
        _start_pos_tracker(self.session, self._item_url, start_pos=0)
        if self._resume_pos > 30:
            self._seek_retry_count = 0
            self._seek_timer.start(6000, True)
        self["osd_title"].setText(self.title)
        self["status"].setText(u"▶ Playing")
        self._total_secs = 0
        self.__showOSD(True)

    def __togglePause(self):
        try:
            svc = self.session.nav.getCurrentService()
            if not svc:
                self.__showOSD(True); return
            p = svc.pause()
            if not p:
                self.__showOSD(True); return
            if self._paused:
                p.unpause()
                self._paused = False
                global _GLOBAL_PLAY_START_WALL, _GLOBAL_PLAY_START_POS
                _GLOBAL_PLAY_START_POS = self._paused_elapsed
                _GLOBAL_PLAY_START_WALL = time.time()
                self["status"].setText(u"▶ Playing")
            else:
                wall = _GLOBAL_PLAY_START_WALL
                base = _GLOBAL_PLAY_START_POS
                if wall:
                    elapsed = int((time.time() - wall) + base)
                else:
                    elapsed = 0
                self._paused_elapsed = max(0, elapsed)
                p.pause()
                self._paused = True
                self["status"].setText(u"⏸ Paused")
            self.__showOSD(True)
        except Exception as e:
            log("togglePause error: {}".format(e))
            self.__showOSD(True)

    def __seek(self, delta_secs):
        try:
            svc = self.session.nav.getCurrentService()
            if not svc: return
            sk = svc.seek()
            if not sk: return
            global _GLOBAL_PLAY_START_WALL, _GLOBAL_PLAY_START_POS, _GLOBAL_LAST_SEEK_TARGET
            _wall = _GLOBAL_PLAY_START_WALL
            _base = _GLOBAL_PLAY_START_POS
            if _wall:
                elapsed = time.time() - _wall
            else:
                elapsed = 0
            current_est = int(_base + elapsed)
            target = max(0, current_est + int(delta_secs))
            _tot = self._total_secs
            if _tot > 0:
                target = min(target, _tot - 3)
            sk.seekTo(target * 90000)
            _GLOBAL_LAST_SEEK_TARGET = target
            _GLOBAL_PLAY_START_POS = max(0, target - 2)
            _GLOBAL_PLAY_START_WALL = time.time()
            if self._paused:
                self._paused_elapsed = target
            self._total_secs = 0
            _th = target // 3600; _tm = (target % 3600) // 60; _ts = target % 60
            _arr = u"➡" if delta_secs > 0 else u"⬅"
            self["status"].setText(u"{} {:02d}:{:02d}:{:02d}".format(_arr, _th, _tm, _ts))
            self.__showOSD(True)
            self._hide_timer.start(2500, True)
        except Exception as e:
            log("seek error: {}".format(e))

    def __onRestart(self):
        log("Restart+Resume requested by green button")
        if self._item_url:
            try:
                if self._paused:
                    secs = self._paused_elapsed
                else:
                    wall = _GLOBAL_PLAY_START_WALL
                    base = _GLOBAL_PLAY_START_POS
                    secs = int((time.time() - wall) + base) if wall else 0
                if secs > 30:
                    save_position(self._item_url, secs)
                    self._resume_pos = secs
            except Exception as e:
                log("Restart pos-save error: {}".format(e))
        try:
            self._seek_timer.stop()
            self._seek_verify_timer.stop()
        except: pass
        self._play_confirmed = False
        self._seek_retry_count = 0
        try:
            self.session.nav.stopService()
        except: pass
        self._candidate_idx = -1
        self["status"].setText(u"إعادة التشغيل + استئناف من {}:{:02d}...".format(
            self._resume_pos // 60, self._resume_pos % 60) if self._resume_pos > 30 else u"إعادة التشغيل...")
        self.__showOSD(True)
        restart_timer = eTimer()
        restart_timer.callback.append(self.__playNext)
        restart_timer.start(500, True)

    def __onExit(self):
        try:
            if self._item_url:
                if self._paused:
                    secs = self._paused_elapsed
                else:
                    wall = _GLOBAL_PLAY_START_WALL
                    base = _GLOBAL_PLAY_START_POS
                    if wall:
                        secs = int((time.time() - wall) + base)
                    else:
                        secs = 0
                _tot = self._total_secs
                if _tot > 0:
                    secs = min(secs, _tot - 5)
                secs = max(0, secs)
                if secs > 30:
                    save_position(self._item_url, secs)
        except Exception as e:
            log("Exit save error: {}".format(e))
        try:
            self.session.nav.stopService()
        except: pass
        _stop_pos_tracker()
        _restore_previous_service(self.session, self.previous_service)
        self.close()

    def __stop(self):
        self.__hideOSD()
        for t in ("_seek_timer","_seek_verify_timer","_retry_timer","_hide_timer","_osd_update_timer","_force_confirmation_timer"):
            try: getattr(self, t).stop()
            except: pass

    def __onFailed(self):
        if self._play_confirmed:
            return
        try:
            self._retry_timer.stop()
            self._force_confirmation_timer.stop()
        except: pass
        log("Play failed event: {}".format(self._candidate_label))
        self.__playNext()

    def __onTimeout(self):
        global _PROXY_LAST_HIT, _PROXY_LAST_BYTES
        if self._play_confirmed:
            return
        if self._candidate_uses_proxy and _PROXY_LAST_HIT >= self._candidate_start_ts and _PROXY_LAST_BYTES > 0:
            log("Play proxy confirmed by traffic: {} bytes".format(_PROXY_LAST_BYTES))
            self.__onConfirmed()
            return
        log("Play timeout: {}".format(self._candidate_label))
        self.__playNext()

    def __doSeek(self):
        if not self._resume_pos or self._resume_pos <= 30:
            return
        try:
            svc = self.session.nav.getCurrentService()
            seek = svc and svc.seek()
            if not seek:
                self._seek_retry_count += 1
                if self._seek_retry_count <= 3:
                    self._seek_timer.start(4000, True)
                return
            seek.seekTo(self._resume_pos * 90000)
            self._total_secs = 0
            self._seek_verify_timer.start(4000, True)
            if self._osd_visible:
                self.__updateOSD()
        except Exception as e:
            self._seek_retry_count += 1
            if self._seek_retry_count <= 3:
                self._seek_timer.start(4000, True)

    def __verifySeek(self):
        if not self._resume_pos or self._resume_pos <= 30:
            return
        global _GLOBAL_PLAY_START_WALL, _GLOBAL_PLAY_START_POS, _GLOBAL_LAST_SEEK_TARGET
        try:
            svc = self.session.nav.getCurrentService()
            seek = svc and svc.seek()
            actual_pos = -1
            if seek:
                try:
                    r = seek.getPlayPosition()
                    if r and r[0] == 0 and r[1] > 0:
                        actual_pos = int(r[1] // 90000)
                except Exception:
                    pass
            if actual_pos >= 0:
                if actual_pos >= max(0, self._resume_pos - 60):
                    _GLOBAL_PLAY_START_POS = actual_pos
                    _GLOBAL_PLAY_START_WALL = time.time()
                    _GLOBAL_LAST_SEEK_TARGET = actual_pos
                    if self._paused:
                        self._paused_elapsed = actual_pos
                else:
                    if seek and self._seek_retry_count <= 3:
                        self._seek_retry_count += 1
                        seek.seekTo(self._resume_pos * 90000)
                        self._seek_verify_timer.start(3000, True)
                    else:
                        _GLOBAL_PLAY_START_POS = max(0, self._resume_pos - 2)
                        _GLOBAL_PLAY_START_WALL = time.time()
            else:
                if self._seek_retry_count <= 2:
                    if seek:
                        seek.seekTo(self._resume_pos * 90000)
                    self._seek_retry_count += 1
                    _GLOBAL_PLAY_START_POS = max(0, self._resume_pos - 2)
                    _GLOBAL_PLAY_START_WALL = time.time()
                    _GLOBAL_LAST_SEEK_TARGET = self._resume_pos
                    if self._paused:
                        self._paused_elapsed = self._resume_pos
                    self._seek_verify_timer.start(3000, True)
        except Exception as e:
            log("verifySeek error: {}".format(e))

def play(session, url, title, resume_pos=0, item_url=""):
    """
    Global play function – decides whether to use built-in player or custom player.
    """
    svc_url = str(url).strip()
    is_remote = svc_url.startswith("http://") or svc_url.startswith("https://")
    previous_service = _capture_previous_service(session)
    if is_remote:
        candidates = build_remote_play_candidates(svc_url)
        session.open(MultiMoviePlayer, title, candidates, previous_service, resume_pos=resume_pos, item_url=item_url)
        return
    # local file or other – use MoviePlayer
    from Screens.InfoBar import MoviePlayer
    sref = eServiceReference(4097, 0, svc_url)
    if sys.version_info[0] == 3:
        sref.setName(str(title))
    else:
        sref.setName(title.encode("utf-8", "ignore"))
    callback = lambda *args: _restore_previous_service(session, previous_service)
    try:
        session.openWithCallback(callback, MoviePlayer, sref)
    except Exception as e:
        log("MoviePlayer fallback error: {}".format(e))