#!/usr/bin/env python3
from base64 import b64encode
from datetime import datetime
from os import path as ospath
from pkg_resources import get_distribution
from aiofiles import open as aiopen
from aiofiles.os import remove as aioremove, path as aiopath, mkdir
from re import match as re_match
from time import time
from html import escape
from uuid import uuid4
from subprocess import run as srun
from asyncio import create_subprocess_exec, create_subprocess_shell, run_coroutine_threadsafe, sleep
from asyncio.subprocess import PIPE
from functools import partial, wraps
from concurrent.futures import ThreadPoolExecutor

from aiohttp import ClientSession as aioClientSession
from psutil import virtual_memory, cpu_percent, disk_usage
from requests import get as rget
from mega import MegaApi
from pyrogram.enums import ChatType
from pyrogram.types import BotCommand
from pyrogram.errors import PeerIdInvalid

from bot.helper.ext_utils.db_handler import DbManger
from bot.helper.themes import BotTheme
from bot import OWNER_ID, bot_name, DATABASE_URL, LOGGER, get_client, aria2, download_dict, download_dict_lock, botStartTime, user_data, config_dict, bot_loop, extra_buttons, user
from bot.helper.telegram_helper.bot_commands import BotCommands
from bot.helper.telegram_helper.button_build import ButtonMaker
from bot.helper.ext_utils.telegraph_helper import telegraph
from bot.helper.ext_utils.shortners import short_url

THREADPOOL = ThreadPoolExecutor(max_workers=1000)

MAGNET_REGEX = r'magnet:\?xt=urn:(btih|btmh):[a-zA-Z0-9]*\s*'

URL_REGEX = r'^(?!\/)(rtmps?:\/\/|mms:\/\/|rtsp:\/\/|https?:\/\/|ftp:\/\/)?([^\/:]+:[^\/@]+@)?(www\.)?(?=[^\/:\s]+\.[^\/:\s]+)([^\/:\s]+\.[^\/:\s]+)(:\d+)?(\/[^#\s]*[\s\S]*)?(\?[^#\s]*)?(#.*)?$'

SIZE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']

STATUS_START = 0
PAGES = 1
PAGE_NO = 1


class MirrorStatus:
    STATUS_UPLOADING = "Uploading"
    STATUS_DOWNLOADING = "Downloading"
    STATUS_CLONING = "Cloning"
    STATUS_QUEUEDL = "DL queued"
    STATUS_QUEUEUP = "UL queued"
    STATUS_PAUSED = "Paused"
    STATUS_ARCHIVING = "Archiving"
    STATUS_EXTRACTING = "Extracting"
    STATUS_SPLITTING = "Splitting"
    STATUS_CHECKING = "CheckUp"
    STATUS_SEEDING = "Seeding"
    STATUS_UPLOADDDL = "Upload DDL"


class setInterval:
    def __init__(self, interval, action):
        self.interval = interval
        self.action = action
        self.task = bot_loop.create_task(self.__set_interval())

    async def __set_interval(self):
        while True:
            await sleep(self.interval)
            await self.action()

    def cancel(self):
        self.task.cancel()


def get_readable_file_size(size_in_bytes):
    if size_in_bytes is None:
        return '0B'
    index = 0
    while size_in_bytes >= 1024 and index < len(SIZE_UNITS) - 1:
        size_in_bytes /= 1024
        index += 1
    return f'{size_in_bytes:.2f}{SIZE_UNITS[index]}' if index > 0 else f'{size_in_bytes}B'


async def getDownloadByGid(gid):
    async with download_dict_lock:
        return next((dl for dl in download_dict.values() if dl.gid() == gid), None)


async def getAllDownload(req_status, user_id=None):
    dls = []
    async with download_dict_lock:
        for dl in list(download_dict.values()):
            if user_id and user_id != dl.message.from_user.id:
                continue
            status = dl.status()
            if req_status in ['all', status]:
                dls.append(dl)
    return dls


async def get_user_tasks(user_id, maxtask):
    if tasks := await getAllDownload('all', user_id):
        return len(tasks) >= maxtask


def bt_selection_buttons(id_):
    gid = id_[:8]
    pincode = ''.join([n for n in id_ if n.isdigit()][:4])
    buttons = ButtonMaker()
    BASE_URL = config_dict['BASE_URL']
    if config_dict['WEB_PINCODE']:
        buttons.ubutton("Select Files", f"{BASE_URL}/app/files/{id_}")
        buttons.ibutton("Pincode", f"btsel pin {gid} {pincode}")
    else:
        buttons.ubutton("Select Files", f"{BASE_URL}/app/files/{id_}?pin_code={pincode}")
    buttons.ibutton("Cancel", f"btsel rm {gid} {id_}")
    buttons.ibutton("Done Selecting", f"btsel done {gid} {id_}")
    return buttons.build_menu(2)


async def get_telegraph_list(telegraph_content):
    path = [(await telegraph.create_page(title="Drive Search", content=content))["path"] for content in telegraph_content]
    if len(path) > 1:
        await telegraph.edit_telegraph(path, telegraph_content)
    buttons = ButtonMaker()
    buttons.ubutton("VIEW", f"https://telegra.ph/{path[0]}")
    buttons = extra_btns(buttons)
    return buttons.build_menu(1)

def handleIndex(index, dic):
    while True:
        if abs(index) < len(dic):
            break
        if index < 0: index = len(dic) - abs(index)
        elif index > 0: index = index - len(dic)
    return index

def get_progress_bar_string(pct):
    SYM_LIST = ['▏', '▎', '▍', '▌', '▋', '▊', '▉', '█']

    pct = min(max(float(str(pct).strip('%')), 0), 100)
    total_blocks, filled_blocks = 10, int(pct * 10 / 100)
    remaining_blocks = total_blocks - filled_blocks

    filled_str = '█' * filled_blocks
    remaining_str = '  ' * remaining_blocks

    if remaining_blocks > 0:
        remaining_pct = ((pct * total_blocks) % 100) / 100
        idx = min(int(remaining_pct * len(SYM_LIST)), len(SYM_LIST) - 1)
        filled_str += SYM_LIST[idx]
        remaining_str = remaining_str[1:]

    return filled_str + remaining_str

class EngineStatus:
    STATUS_ARIA = "Aria2"
    STATUS_GD = "G-API"
    STATUS_MEGA = "MegaSDK"
    STATUS_QB = "qBit"
    STATUS_TG = "Pyro"
    STATUS_YT = "yt-dlp"
    STATUS_EXT = "pExtract"
    STATUS_SPLIT_MERGE = "ffmpeg"
    STATUS_ZIP = "p7zip"
    STATUS_QUEUE = "Sleep v0"
    STATUS_RCLONE = "Rclone"

def source(self):
    if sender_chat := self.message.sender_chat:
        source = sender_chat.title
    else:
        source = self.message.from_user.username or self.message.from_user.id
    if reply_to := self.message.reply_to_message:
        if sender_chat := reply_to.sender_chat:
            source = reply_to.sender_chat.title
        elif not reply_to.from_user.is_bot:
            source = reply_to.from_user.username or reply_to.from_user.id
    return source
            

def get_readable_message():
    msg = ''
    button = None
    STATUS_LIMIT = config_dict['STATUS_LIMIT']
    tasks = len(download_dict)
    currentTime = get_readable_time(time() - botStartTime)
    if config_dict['BOT_MAX_TASKS']:
        bmax_task = f"/{config_dict['BOT_MAX_TASKS']}"
    else:
        bmax_task = ''
    globals()['PAGES'] = (tasks + STATUS_LIMIT - 1) // STATUS_LIMIT
    if PAGE_NO > PAGES and PAGES != 0:
        globals()['STATUS_START'] = STATUS_LIMIT * (PAGES - 1)
        globals()['PAGE_NO'] = PAGES
    for download in list(download_dict.values())[STATUS_START:STATUS_LIMIT+STATUS_START]:
        msg += f"{escape(f'{download.name()}')}\n"
        msg += f"by {source(download)}\n\n"
        msg += f"<b>┌ {download.status()}...</b>"
        if download.status() not in [MirrorStatus.STATUS_SPLITTING, MirrorStatus.STATUS_SEEDING]:
            msg += f"\n<b>├ {get_progress_bar_string(download.progress())}</b> {download.progress()}"
            msg += f"\n<b>├ </b>{download.processed_bytes()} of {download.size()}"
            msg += f"\n<b>├ Speed</b>: {download.speed()}"
            msg += f'\n<b>├ Estimated</b>: {download.eta()}'
            if hasattr(download, 'seeders_num'):
                try:
                    msg += f"\n<b>├ Seeders</b>: {download.seeders_num()} | <b>Leechers</b>: {download.leechers_num()}"
                except:
                    pass
        elif download.status() == MirrorStatus.STATUS_SEEDING:
            msg += f"\n<b>├ Size</b>: {download.size()}"
            msg += f"\n<b>├ Speed</b>: {download.upload_speed()}"
            msg += f"\n<b>├ Uploaded</b>: {download.uploaded_bytes()}"
            msg += f"\n<b>├ Ratio</b>: {download.ratio()}"
            msg += f"\n<b>├ Time</b>: {download.seeding_time()}"
        else:
            msg += f"\n<b>├ Size</b>: {download.size()}"
        msg += f"\n<b>├ Elapsed</b>: {get_readable_time(time() - download.message.date.timestamp())}"
        msg += f"\n<b>├ Mode</b>: {download.upload_details['mode']}"
        msg += f"\n<b>└ </b><code>/{BotCommands.CancelMirror} {download.gid()}</code>\n\n"
    if len(msg) == 0:
        return None, None
    dl_speed = 0
    up_speed = 0
    for download in download_dict.values():
            tstatus = download.status()
            if tstatus == MirrorStatus.STATUS_DOWNLOADING:
                spd = download.speed()
                if 'K' in spd:
                    dl_speed += float(spd.split('K')[0]) * 1024
                elif 'M' in spd:
                    dl_speed += float(spd.split('M')[0]) * 1048576
            elif tstatus == MirrorStatus.STATUS_UPLOADING:
                spd = download.speed()
                if 'K' in spd:
                    up_speed += float(spd.split('K')[0]) * 1024
                elif 'M' in spd:
                    up_speed += float(spd.split('M')[0]) * 1048576
            elif tstatus == MirrorStatus.STATUS_SEEDING:
                spd = download.upload_speed()
                if 'K' in spd:
                    up_speed += float(spd.split('K')[0]) * 1024
                elif 'M' in spd:
                    up_speed += float(spd.split('M')[0]) * 1048576
    if tasks > STATUS_LIMIT:
        buttons = ButtonMaker()
        buttons.ibutton("Prev", "status pre")
        buttons.ibutton(f"{PAGE_NO}/{PAGES}", "status ref")
        buttons.ibutton("Next", "status nex")
        button = buttons.build_menu(3)
    msg += f"<b>• Tasks</b>: {tasks}{bmax_task}"
    msg += f"\n<b>• Bot uptime</b>: {currentTime}"
    msg += f"\n<b>• Free disk space</b>: {get_readable_file_size(disk_usage(config_dict['DOWNLOAD_DIR']).free)}"
    msg += f"\n<b>• Uploading speed</b>: {get_readable_file_size(up_speed)}/s"
    msg += f"\n<b>• Downloading speed</b>: {get_readable_file_size(dl_speed)}/s"
    return msg, button


async def turn_page(data):
    STATUS_LIMIT = config_dict['STATUS_LIMIT']
    global STATUS_START, PAGE_NO
    async with download_dict_lock:
        if data[1] == "nex":
            if PAGE_NO == PAGES:
                STATUS_START = 0
                PAGE_NO = 1
            else:
                STATUS_START += STATUS_LIMIT
                PAGE_NO += 1
        elif data[1] == "pre":
            if PAGE_NO == 1:
                STATUS_START = STATUS_LIMIT * (PAGES - 1)
                PAGE_NO = PAGES
            else:
                STATUS_START -= STATUS_LIMIT
                PAGE_NO -= 1


def get_readable_time(seconds):
    periods = [('cosmic year', 31557600000000000), ('galactic year', 225000000000000000), ('aeon', 31536000000000000), ('epoch', 315360000000), ('millennium', 31536000000), ('century', 3153600000), ('decade', 315360000), ('year', 31536000), ('month', 2592000), ('week', 604800), ('day', 86400), ('hour', 3600), ('minute', 60), ('second', 1)]
    result = ''
    for period_name, period_seconds in periods:
        if seconds >= period_seconds:
            period_value, seconds = divmod(seconds, period_seconds)
            plural_suffix = 's' if period_value > 1 else ''
            result += f'{int(period_value)} {period_name}{plural_suffix} '
            if len(result.split()) == 2:
                break
    return result.strip()

def is_magnet(url):
    return bool(re_match(MAGNET_REGEX, url))


def is_url(url):
    return bool(re_match(URL_REGEX, url))


def is_gdrive_link(url):
    return "drive.google.com" in url


def is_telegram_link(url):
    return url.startswith(('https://t.me/', 'tg://openmessage?user_id='))


def is_share_link(url):
    return bool(re_match(r'https?:\/\/.+\.gdtot\.\S+|https?:\/\/(filepress|filebee|appdrive|gdflix)\.\S+', url))


def is_mega_link(url):
    return "mega.nz" in url or "mega.co.nz" in url


def is_rclone_path(path):
    return bool(re_match(r'^(mrcc:)?(?!magnet:)(?![- ])[a-zA-Z0-9_\. -]+(?<! ):(?!.*\/\/).*$|^rcl$', path))


def get_mega_link_type(url):
    return "folder" if "folder" in url or "/#F!" in url else "file"


def arg_parser(items, arg_base):
    if not items:
        return arg_base
    bool_arg_set = {'-b', '-e', '-z', '-s', '-j', '-d'}
    t = len(items)
    i = 0
    arg_start = -1

    while i + 1 <= t:
        part = items[i].strip()
        if part in arg_base:
            if arg_start == -1:
                arg_start = i
            if i + 1 == t and part in bool_arg_set or part in ['-s', '-j']:
                arg_base[part] = True
            else:
                sub_list = []
                for j in range(i + 1, t):
                    item = items[j].strip()
                    if item in arg_base:
                        if part in bool_arg_set and not sub_list:
                            arg_base[part] = True
                        break
                    sub_list.append(item.strip())
                    i += 1
                if sub_list:
                    arg_base[part] = " ".join(sub_list)
        i += 1

    link = []
    if items[0].strip() not in arg_base:
        if arg_start == -1:
            link.extend(item.strip() for item in items)
        else:
            link.extend(items[r].strip() for r in range(arg_start))
        if link:
            arg_base['link'] = " ".join(link)
    return arg_base


async def get_content_type(url):
    try:
        async with aioClientSession(trust_env=True) as session:
            async with session.get(url, verify_ssl=False) as response:
                return response.headers.get('Content-Type')
    except:
        return None


def update_user_ldata(id_, key=None, value=None):
    exception_keys = ['is_sudo', 'is_auth', 'dly_tasks']
    if not key and not value:
        if id_ in user_data:
            updated_data = {k: v for k, v in user_data[id_].items() if k in exception_keys}
            user_data[id_] = updated_data
        return
    user_data.setdefault(id_, {})
    user_data[id_][key] = value


async def download_image_url(url):
    path = "Images/"
    if not await aiopath.isdir(path):
        await mkdir(path)
    image_name = url.split('/')[-1]
    des_dir = ospath.join(path, image_name)
    async with aioClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                async with aiopen(des_dir, 'wb') as file:
                    async for chunk in response.content.iter_chunked(1024):
                        await file.write(chunk)
                LOGGER.info(f"Image Downloaded Successfully as {image_name}")
            else:
                LOGGER.error(f"Failed to Download Image from {url}")
    return des_dir


async def cmd_exec(cmd, shell=False):
    if shell:
        proc = await create_subprocess_shell(cmd, stdout=PIPE, stderr=PIPE)
    else:
        proc = await create_subprocess_exec(*cmd, stdout=PIPE, stderr=PIPE)
    stdout, stderr = await proc.communicate()
    stdout = stdout.decode().strip()
    stderr = stderr.decode().strip()
    return stdout, stderr, proc.returncode


def new_task(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        return bot_loop.create_task(func(*args, **kwargs))
    return wrapper


async def sync_to_async(func, *args, wait=True, **kwargs):
    pfunc = partial(func, *args, **kwargs)
    future = bot_loop.run_in_executor(THREADPOOL, pfunc)
    return await future if wait else future


def async_to_sync(func, *args, wait=True, **kwargs):
    future = run_coroutine_threadsafe(func(*args, **kwargs), bot_loop)
    return future.result() if wait else future


def new_thread(func):
    @wraps(func)
    def wrapper(*args, wait=False, **kwargs):
        future = run_coroutine_threadsafe(func(*args, **kwargs), bot_loop)
        return future.result() if wait else future
    return wrapper


async def getdailytasks(user_id, increase_task=False, upleech=0, upmirror=0, check_mirror=False, check_leech=False):
    task, lsize, msize = 0, 0, 0
    if user_id in user_data and user_data[user_id].get('dly_tasks'):
        userdate, task, lsize, msize = user_data[user_id]['dly_tasks']
        nowdate = datetime.now()
        if userdate.year <= nowdate.year and userdate.month <= nowdate.month and userdate.day < nowdate.day:
            task, lsize, msize = 0, 0, 0
            if increase_task:
                task = 1
            elif upleech != 0:
                lsize += upleech
            elif upmirror != 0:
                msize += upmirror
        elif increase_task:
            task += 1
        elif upleech != 0:
            lsize += upleech
        elif upmirror != 0:
            msize += upmirror
    elif increase_task:
        task += 1
    elif upleech != 0:
        lsize += upleech
    elif upmirror != 0:
        msize += upmirror
    update_user_ldata(user_id, 'dly_tasks', [datetime.now(), task, lsize, msize])
    if DATABASE_URL:
        await DbManger().update_user_data(user_id)
    if check_leech:
        return lsize
    elif check_mirror:
        return msize
    return task


def checking_access(user_id, button=None):
    token_timeout = config_dict['TOKEN_TIMEOUT']
    if not token_timeout or bool(user_id == OWNER_ID or user_id in user_data and user_data[user_id].get('is_sudo')):
        return None, button
    user_data.setdefault(user_id, {})
    data = user_data[user_id]
    expire = data.get('time')
    isExpired = (expire is None or expire is not None and (time() - expire) > token_timeout)
    if isExpired:
        token = data['token'] if expire is None and 'token' in data else str(uuid4())
        if expire is not None:
            del data['time']
        data['token'] = token
        user_data[user_id].update(data)
        time_str = format_validity_time(token_timeout)
        if button is None:
            button = ButtonMaker()
        encrypt_url = b64encode(f"{token}&&{user_id}".encode()).decode()
        button.ubutton('Collect token', isgd(short_url(f'https://t.me/{bot_name}?start={encrypt_url}')))
        return f'Your token has expired, please collect a new token.\n<b>It will expire after {time_str}</b>!', button
    return None, button


def format_validity_time(seconds):
    periods = [('cosmic year', 31557600000000000), ('galactic year', 225000000000000000), ('aeon', 31536000000000000), ('epoch', 315360000000), ('millennium', 31536000000), ('century', 3153600000), ('decade', 315360000), ('year', 31536000), ('month', 2592000), ('week', 604800), ('day', 86400), ('hour', 3600), ('minute', 60), ('second', 1)]
    result = ''
    for period_name, period_seconds in periods:
        if seconds >= period_seconds:
            period_value, seconds = divmod(seconds, period_seconds)
            plural_suffix = 's' if period_value > 1 else ''
            result += f'{int(period_value)} {period_name}{plural_suffix} '
    return result

def extra_btns(buttons):
    if extra_buttons:
        for btn_name, btn_url in extra_buttons.items():
            buttons.ubutton(btn_name, btn_url)
    return buttons

async def set_commands(client):
    if config_dict['SET_COMMANDS']:
        await client.set_bot_commands(
            [
                BotCommand(
                    f'{BotCommands.MirrorCommand[0]}',
                    f'or /{BotCommands.MirrorCommand[1]} Mirror',
                ),
                BotCommand(
                    f'{BotCommands.LeechCommand[0]}',
                    f'or /{BotCommands.LeechCommand[1]} Leech',
                ),
                BotCommand(
                    f'{BotCommands.QbMirrorCommand[0]}',
                    f'or /{BotCommands.QbMirrorCommand[1]} Mirror torrent using qBittorrent',
                ),
                BotCommand(
                    f'{BotCommands.QbLeechCommand[0]}',
                    f'or /{BotCommands.QbLeechCommand[1]} Leech torrent using qBittorrent',
                ),
                BotCommand(
                    f'{BotCommands.YtdlCommand[0]}',
                    f'or /{BotCommands.YtdlCommand[1]} Mirror yt-dlp supported link',
                ),
                BotCommand(
                    f'{BotCommands.YtdlLeechCommand[0]}',
                    f'or /{BotCommands.YtdlLeechCommand[1]} Leech through yt-dlp supported link',
                ),
                BotCommand(
                    f'{BotCommands.CloneCommand}', 'Copy file/folder to Drive'
                ),
                BotCommand(
                    f'{BotCommands.CountCommand}',
                    '[drive_url]: Count file/folder of Google Drive.',
                ),
                BotCommand(
                    f'{BotCommands.StatusCommand[0]}',
                    f'or /{BotCommands.StatusCommand[1]} Get mirror status message',
                ),
                BotCommand(
                    f'{BotCommands.StatsCommand}', 'Check Bot & System stats'
                ),
                BotCommand(
                    f'{BotCommands.BtSelectCommand}',
                    'Select files to download only torrents',
                ),
                BotCommand(f'{BotCommands.CancelMirror}', 'Cancel a Task'),
                BotCommand(
                    f'{BotCommands.CancelAllCommand[0]}',
                    'Cancel all tasks which added by you to in bots.',
                ),
                BotCommand(f'{BotCommands.ListCommand}', 'Search in Drive'),
                BotCommand(
                    f'{BotCommands.SearchCommand}', 'Search in Torrent'
                ),
                BotCommand(
                    f'{BotCommands.UserSetCommand[0]}', 'Users settings'
                ),
                BotCommand(f'{BotCommands.HelpCommand}', 'Get detailed help'),
                BotCommand(f'{BotCommands.BotSetCommand}', 'Open Bot settings'),
                BotCommand(f'{BotCommands.LogCommand}', 'View log'),
                BotCommand(f'{BotCommands.RestartCommand[0]}', 'Restart bot')
            ]
        )


def is_valid_token(url, token):
    resp = rget(url=f"{url}getAccountDetails?token={token}&allDetails=true").json()
    if resp["status"] == "error-wrongToken":
        raise Exception("Invalid Gofile Token, Get your Gofile token from --> https://gofile.io/myProfile")
        
        
def isgd(long_url):
    url = 'https://is.gd/create.php'
    params = {
        'format': 'simple',
        'url': long_url,
    }
    try:
        resp = rget(url, params=params)
        return resp.text
    except Exception:
        return long_url