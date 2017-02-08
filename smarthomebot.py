#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""

    Smart Home Bot for Telegram.

    Copyright (c) 2017 Oliver Lau <oliver@ersatzworld.net>
    All rights reserved.

"""

import sys
import os
import datetime
import json
import time
import random
import telepot
import subprocess
import shelve
import urllib.request
from tempfile import mkstemp
from telepot.namedtuple import InlineKeyboardMarkup, InlineKeyboardButton
from telepot.delegate import per_chat_id_in, create_open, pave_event_space, include_callback_query_chat_id
from pprint import pprint
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.job import Job
from PIL import Image


APPNAME = "smarthomebot"


class easydict(dict):
    def __missing__(self, key):
        self[key] = easydict()
        return self[key]


def make_snapshot(urls, bot, chat_id):
    for url in urls:
        handle, photo_filename = mkstemp(prefix="snapshot-", suffix=".jpg")
        response = None
        try:
            response = urllib.request.urlopen(url)
        except urllib.error.URLError as e:
            bot.sendMessage(chat_id, "Fehler beim Abrufen des Schnappschusses via {}: {}".format(url, e.reason))
        if response is None:
            return
        f = open(photo_filename, "wb+")
        f.write(response.read())
        f.close()
        bot.sendPhoto(chat_id, open(photo_filename, "rb"), caption=datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S"))
        os.remove(photo_filename)


class UploadDirectoryEventHandler(FileSystemEventHandler):

    def __init__(self, *args, **kwargs):
        super(FileSystemEventHandler, self).__init__()
        self.bot = kwargs["bot"]
        self.verbose = kwargs["verbose"]
        self.image_folder = kwargs["image_folder"]
        self.authorized_users = kwargs["authorized_users"] or []
        self.path_to_ffmpeg = kwargs["path_to_ffmpeg"]
        self.max_photo_size = kwargs["max_photo_size"]

    def dispatch(self, event):
        if event.event_type == "created" and not event.is_directory:
            filename, ext = os.path.splitext(os.path.basename(event.src_path))
            ext = ext.lower()
            if ext in [".jpg", ".png"]:
                self.send_photo(event.src_path)
            elif ext in [".avi", ".mp4", ".mkv", ".m4v", ".mov", ".mpg"]:
                self.send_video(event.src_path)

    def send_photo(self, src_photo_filename):
        while os.stat(src_photo_filename).st_size == 0:  # make sure file is written
            time.sleep(0.1)
        if self.verbose:
            print("New photo file detected: {}".format(src_photo_filename))
        dst_photo_filename = src_photo_filename
        if self.max_photo_size:
            im = Image.open(src_photo_filename)
            if im.width > self.max_photo_size or im.height > self.max_photo_size:
                im.thumbnail((self.max_photo_size, self.max_photo_size), Image.BILINEAR)
                handle, dst_photo_filename = mkstemp(prefix="smarthomebot-", suffix=".jpg")
                if self.verbose:
                    print("resizing photo to {} ...".format(dst_photo_filename))
                im.save(dst_photo_filename, format="JPEG", quality=87)
                os.remove(src_photo_filename)
            im.close()
        """
        for user in self.authorized_users:
            if self.verbose:
                print("Sending photo {} ...".format(dst_photo_filename))
            self.bot.sendPhoto(user, open(dst_photo_filename, "rb"),
                               caption=datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S"))
        """
        os.remove(dst_photo_filename)

    def send_video(self, src_video_filename):
        global alerting_on
        while os.stat(src_video_filename).st_size == 0:  # make sure file is written
            time.sleep(0.1)
        if self.verbose:
            print("New video file detected: {}".format(src_video_filename))
        if alerting_on and self.path_to_ffmpeg:
            handle, dst_video_filename = mkstemp(prefix="smarthomebot-", suffix=".mp4")
            if self.verbose:
                print("Converting video {} to {} ...".format(src_video_filename, dst_video_filename))
            subprocess.call(
                [self.path_to_ffmpeg,
                 "-y",
                 "-loglevel",
                 "panic",
                 "-i",
                 src_video_filename,
                 "-vf",
                 "scale=480:320",
                 "-movflags",
                 "+faststart",
                 "-c:v",
                 "libx264",
                 "-preset",
                 "fast",
                 dst_video_filename],
                shell=False)
            for user in self.authorized_users:
                self.bot.sendVideo(user, open(dst_video_filename, "rb"),
                                   caption="{} ({})".format(os.path.basename(src_video_filename),
                                                            datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")))
            os.remove(dst_video_filename)
        os.remove(src_video_filename)


class ChatUser(telepot.helper.ChatHandler):

    IdleMessages = ["tüdelü ...", "*gähn", "Mir ist langweilig.", "Chill dein Life! Alles cool hier.",
                    "Hier ist tote Hose.", "Nichts passiert ...", "Scheint niemand zu Hause zu sein.",
                    "Hallo-o!!!"]

    def __init__(self, *args, **kwargs):
        global verbose, cameras
        super(ChatUser, self).__init__(*args, **kwargs)
        self.timeout_secs = 10
        if "timeout" in kwargs.keys():
            self.timeout_secs = kwargs["timeout"]
        self.verbose = verbose
        self.cameras = cameras

    def open(self, initial_msg, seed):
        content_type, chat_type, chat_id = telepot.glance(initial_msg)
        self.on_chat_message(initial_msg)
        self.init_scheduler(chat_id)
        return True

    def init_scheduler(self, chat_id):
        global settings, scheduler, job
        interval = settings[chat_id]["snapshot"]["interval"]
        if type(interval) is not int:
            interval = 0
            settings[chat_id]["snapshot"]["interval"] = interval
        if interval > 0:
            if type(job) is Job:
                job.remove()
            urls = [ cameras[c]["snapshot_url"] for c in self.cameras.keys() ]
            job = scheduler.add_job(make_snapshot, "interval",
                                    seconds=interval,
                                    kwargs={"bot": self.bot, "chat_id": chat_id, "urls": urls})
            scheduler.resume()

    def on__idle(self, event):
        ridx = random.randint(0, len(ChatUser.IdleMessages) - 1)
        self.sender.sendMessage(ChatUser.IdleMessages[ridx])

    def on_close(self, msg):
        if self.verbose:
            print("on_close() called.")
        return True

    def send_snapshot_menu(self):
        kbd = [ InlineKeyboardButton(text=self.cameras[c]["name"], callback_data=c) for c in self.cameras.keys() ]
        keyboard = InlineKeyboardMarkup(inline_keyboard=[kbd])
        self.sender.sendMessage("Schnappschuss anzeigen von:", reply_markup=keyboard)

    def send_main_menu(self):
        global alerting_on
        kbd = [
            InlineKeyboardButton(text=chr(0x1F4F7) + "Schnappschuss",
                                 callback_data="snapshot"),
            InlineKeyboardButton(text=[chr(0x25B6) + chr(0xFE0F) + "Alarme ein", chr(0x23F9) + "Alarme aus"][alerting_on],
                                 callback_data=["enable", "disable"][alerting_on])
            ]
        keyboard = InlineKeyboardMarkup(inline_keyboard=[kbd])
        self.sender.sendMessage("Wähle eine Aktion:", reply_markup=keyboard)

    def on_callback_query(self, msg):
        global alerting_on
        query_id, from_id, query_data = telepot.glance(msg, flavor="callback_query")
        print("Callback Query:", query_id, from_id, query_data)
        if query_data in self.cameras.keys():
            self.bot.answerCallbackQuery(query_id,
                                         text="Schnappschuss von deiner Kamera ‘{}‘".format(query_data))
            if query_data in self.cameras.keys():
                url = cameras[query_data]["snapshot_url"]
                make_snapshot([url], self.bot, from_id)
                self.send_snapshot_menu()
        elif query_data == "disable":
            alerting_on = False
            self.bot.answerCallbackQuery(query_id, text="Alarme wurden ausgeschaltet.")
            self.send_main_menu()
        elif query_data == "enable":
            alerting_on = True
            self.bot.answerCallbackQuery(query_id, text="Alarme wurden eingeschaltet.")
            self.send_main_menu()
        elif query_data == "snapshot":
            self.bot.answerCallbackQuery(query_id)
            self.send_snapshot_menu()

    def on_chat_message(self, msg):
        global scheduler, job, settings, alerting_on
        content_type, chat_type, chat_id = telepot.glance(msg)
        if content_type == "text":
            if self.verbose:
                pprint(msg)
            msg_text = msg["text"]
            if msg_text.startswith("/start"):
                self.sender.sendMessage("*Hallo, ich bin dein Heimüberwachungs-Bot!* " + chr(0x1F916) + "\n\n"
                                        "Ich benachrichtige dich, wenn deine Webcams Bewegungen "
                                        "und laute Geräusche erkannt haben "
                                        "und sende dir ein Video von dem Vorfall.\n",
                                        parse_mode="Markdown")
                self.send_main_menu()
            elif msg_text.startswith("/enable"):
                alerting_on = True
                self.sender.sendMessage("Alarme ein.")
            elif msg_text.startswith("/disable"):
                alerting_on = False
                self.sender.sendMessage("Alarme aus.")
            elif msg_text.startswith("/toggle"):
                alerting_on = not alerting_on
                self.sender.sendMessage("Alarme sind nun {}geschaltet.".format(["aus", "ein"][alerting_on]))
            elif msg_text.startswith("/snapshot"):
                c = msg_text.split()[1:]
                if len(c) == 0:
                    self.send_snapshot_menu()
                else:
                    if c[0] == "interval":
                        if len(c) > 1:
                            interval = int(c[1])
                            settings[chat_id]["snapshot"]["interval"] = interval
                            if interval > 0:
                                scheduler.resume()
                                if type(job) is Job:
                                    job.remove()
                                urls = [ cameras[c]["snapshot_url"] for c in self.cameras.keys() ]
                                job = scheduler.add_job(make_snapshot, "interval",
                                                        seconds=interval,
                                                        kwargs={"bot": self.bot, "chat_id": chat_id, "urls": urls})
                                self.sender.sendMessage("Schnappschussintervall ist auf {} Sekunden eingestellt."
                                                        .format(interval))
                            else:
                                scheduler.pause()
                                self.sender.sendMessage("Zeitgesteuerte Schnappschüsse sind deaktiviert.")
                        else:
                            if settings[chat_id]["snapshot"]["interval"] == {}:
                                self.sender.sendMessage("Schnappschussintervall wurde noch nicht eingestellt.")
                            else:
                                self.sender.sendMessage("Schnappschussintervall ist derzeit auf {} Sekunden eingestellt."
                                                        .format(settings[chat_id]["snapshot"]["interval"]))

            elif msg_text.startswith("/help"):
                self.sender.sendMessage("Verfügbare Kommandos:\n\n"
                                        "/help diese Nachricht anzeigen\n"
                                        "/enable /disable /toggle Benachrichtigungen aktivieren/deaktivieren\n"
                                        "/snapshot Liste der Kameras anzeigen, die Schnappschüsse liefern können\n"
                                        "/snapshot `interval` Das Zeitintervall (Sek.) anzeigen, in dem Schnappschüsse von den Kameras abgerufen und angezeigt werden sollen\n"
                                        "/snapshot `interval` `secs` Schnappschussintervall auf `secs` Sekunden setzen (`0` für aus)\n"
                                        "/start den Bot (neu)starten\n",
                                        parse_mode="Markdown")
            elif msg_text.startswith("/"):
                self.sender.sendMessage("Unbekanntes Kommando. /help für weitere Infos eintippen.")
            else:
                self.sender.sendMessage("Ich bin nicht sehr gesprächig. Tippe /help für weitere Infos ein.")
        else:
            self.sender.sendMessage("Dein '{}' ist im Nirwana gelandet ...".format(content_type))


# global variables needed for ChatHandler (which unfortunately doesn't allow extra **kwargs)
authorized_users = None
cameras = None
verbose = False
settings = easydict()
scheduler = BackgroundScheduler()
job = None
bot = None
alerting_on = True


def main(arg):
    global bot, authorized_users, cameras, verbose, settings, scheduler, job
    path_to_ffmpeg = None
    timeout_secs = 10 * 60
    image_folder = "/home/ftp-upload"
    max_photo_size = 1280
    telegram_bot_token = None
    config_filename = "smarthomebot-config.json"
    shelf = shelve.open(".smarthomebot.shelf")
    if APPNAME in shelf.keys():
        settings = easydict(shelf[APPNAME])

    try:
        with open(config_filename, "r") as config_file:
            config = json.load(config_file)
    except FileNotFoundError:
        print("Error: config file '{}' not found: {}"
              .format(config_filename))
        return
    except json.decoder.JSONDecodeError as e:
        print("Error: invalid config file '{}': {} in line {} column {} (position {})"
              .format(config_filename, e.msg, e.lineno, e.colno, e.pos))
        return

    if "telegram_bot_token" in config.keys():
        telegram_bot_token = config["telegram_bot_token"]
    if not telegram_bot_token:
        print("Error: config file doesn't contain a `telegram_bot_token`")
        return
    if "authorized_users" in config.keys():
        authorized_users = config["authorized_users"]
    if type(authorized_users) is not list or len(authorized_users) == 0:
        print("Error: config file doesn't contain an `authorized_users` list")
        return
    if "timeout_secs" in config.keys():
        timeout_secs = config["timeout_secs"]
    if "cameras" in config.keys():
        cameras = config["cameras"]
    if "image_folder" in config.keys():
        image_folder = config["image_folder"]
    if "path_to_ffmpeg" in config.keys():
        path_to_ffmpeg = config["path_to_ffmpeg"]
    if "max_photo_size" in config.keys():
        max_photo_size = config["max_photo_size"]
    if "verbose" in config.keys():
        verbose = config["verbose"]
    bot = telepot.DelegatorBot(telegram_bot_token, [
        include_callback_query_chat_id(pave_event_space())(per_chat_id_in(authorized_users, types="private"),
                                                           create_open,
                                                           ChatUser,
                                                           timeout=timeout_secs)
    ])
    if verbose:
       print("Monitoring {} ...".format(image_folder))
    scheduler.start(paused=True)
    event_handler = UploadDirectoryEventHandler(
        image_folder=image_folder,
        verbose=verbose,
        authorized_users=authorized_users,
        path_to_ffmpeg=path_to_ffmpeg,
        max_photo_size=max_photo_size,
        bot=bot)
    observer = Observer()
    observer.schedule(event_handler, image_folder, recursive=True)
    observer.start()
    try:
        bot.message_loop(run_forever="Bot listening ...")
    except KeyboardInterrupt:
        pass
    if verbose:
        print("Exiting ...")
    observer.stop()
    observer.join()
    shelf[APPNAME] = settings
    shelf.sync()
    shelf.close()
    scheduler.shutdown()


if __name__ == "__main__":
    main(sys.argv)
