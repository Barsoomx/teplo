"""Reports keyboard and mouse presence of this Windows session to teplo's MQTT broker like an ESPHome node.

Runs on the office computer under Python 3.12 with the standard library only, in the owner's session, with its
config pc_presence.toml beside it (docs/deploy.md, The office computer).
"""

import ctypes
import logging
import logging.handlers
import re
import socket
import sys
import time
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

DEFAULT_NODE = 'teplo-pc-office'
DEFAULT_PORT = 1883
REPORT_INTERVAL = 30.0
TICK = 1.0
KEEPALIVE = 60
PING_TIMEOUT = 10.0
CONNECT_TIMEOUT = 10.0
BACKOFF_FIRST = 1.0
BACKOFF_MAX = 30.0
NODE_PATTERN = re.compile(r'teplo-[a-z0-9-]+')
ONLINE = b'online'
OFFLINE = b'offline'
MUTEX_NAME = 'Local\\teplo-pc-presence'
ERROR_ALREADY_EXISTS = 183
ERROR_ACCESS_DENIED = 5
EXIT_CONFIG = 2
EXIT_RUNNING = 3

LAST_PORT = 65535
MAX_REMAINING_LENGTH = 268_435_455
MAX_FIELD_LENGTH = 65_535
CONNECT = 0x10
PUBLISH = 0x30
PROTOCOL_LEVEL = 4
CONNECT_FLAGS = 0xC6
CONNACK = b'\x20\x02'
CONNACK_LENGTH = 4
PINGREQ = b'\xc0\x00'
PINGRESP = b'\xd0\x00'
DISCONNECT = b'\xe0\x00'
REFUSALS = {
    1: 'unacceptable protocol version',
    2: 'identifier rejected',
    3: 'server unavailable',
    4: 'bad user name or password',
    5: 'not authorized',
}
LOG_BYTES = 1_000_000
LOG_FORMAT = '%(asctime)s %(levelname)s %(message)s'


class ConfigError(Exception):
    pass


class MqttError(Exception):
    pass


class MqttRefusedError(MqttError):
    def __init__(self, code: int) -> None:
        super().__init__(code)
        self._code = code

    @property
    def code(self) -> int:
        return self._code

    def __str__(self) -> str:
        return REFUSALS.get(self._code, f'refused with code {self._code}')


@dataclass(frozen=True, kw_only=True)
class Config:
    host: str
    port: int
    username: str
    password: str = field(repr=False)
    node: str

    @property
    def status_topic(self) -> str:
        return f'teplo/{self.node}/status'

    @property
    def presence_topic(self) -> str:
        return f'teplo/{self.node}/sensor/presence/state'


def default_config_path() -> Path:
    return Path(__file__).with_name('pc_presence.toml')


def load_config(path: Path) -> Config:
    table = _table(path)
    return Config(
        host=_required(table, 'host').strip(),
        port=_port(table.get('port', DEFAULT_PORT)),
        username=_required(table, 'username').strip(),
        password=_required(table, 'password'),
        node=_node(table.get('node', DEFAULT_NODE)),
    )


def _table(path: Path) -> dict[str, object]:
    try:
        return tomllib.loads(path.read_bytes().decode('utf-8-sig'))
    except FileNotFoundError as error:
        raise ConfigError(f'{path} does not exist: copy pc_presence.example.toml to it and set the password') from error
    except OSError as error:
        raise ConfigError(f'cannot read {path}: {error}') from error
    except UnicodeDecodeError as error:
        raise ConfigError(f'{path} is not UTF-8 text') from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f'{path} is not valid TOML: {error}') from error


def _required(table: Mapping[str, object], key: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f'{key} must be set')

    return value


def _port(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= LAST_PORT:
        raise ConfigError(f'port must be a whole number from 1 to {LAST_PORT}, not {value!r}')

    return value


def _node(value: object) -> str:
    if not isinstance(value, str) or NODE_PATTERN.fullmatch(value) is None:
        raise ConfigError(f'node must be teplo- and lowercase letters, digits or hyphens, not {value!r}')

    return value


def remaining_length(length: int) -> bytes:
    if not 0 <= length <= MAX_REMAINING_LENGTH:
        raise ValueError(f'an MQTT packet carries 0 to {MAX_REMAINING_LENGTH} bytes, not {length}')

    encoded = bytearray()
    while True:
        length, digit = divmod(length, 128)
        encoded.append(digit | (0x80 if length else 0))
        if not length:
            return bytes(encoded)


def utf8(value: str) -> bytes:
    return binary(value.encode('utf-8'))


def binary(value: bytes) -> bytes:
    if len(value) > MAX_FIELD_LENGTH:
        raise ValueError(f'an MQTT field carries at most {MAX_FIELD_LENGTH} bytes, not {len(value)}')

    return len(value).to_bytes(2, 'big') + value


def connect_packet(config: Config) -> bytes:
    return _packet(
        CONNECT,
        utf8('MQTT')
        + bytes((PROTOCOL_LEVEL, CONNECT_FLAGS))
        + KEEPALIVE.to_bytes(2, 'big')
        + utf8(config.node)
        + utf8(config.status_topic)
        + binary(OFFLINE)
        + utf8(config.username)
        + utf8(config.password),
    )


def publish_packet(topic: str, payload: bytes) -> bytes:
    return _packet(PUBLISH, utf8(topic) + payload)


def _packet(kind: int, body: bytes) -> bytes:
    return bytes((kind,)) + remaining_length(len(body)) + body


def connack_code(packet: bytes) -> int:
    if len(packet) != CONNACK_LENGTH or packet[:2] != CONNACK:
        raise MqttError(f'expected CONNACK, got {packet!r}')

    return packet[3]


class Transport(Protocol):
    def send(self, data: bytes) -> None: ...

    def receive(self, size: int, timeout: float) -> bytes: ...

    def close(self) -> None: ...


class SocketTransport:
    def __init__(self, connection: socket.socket) -> None:
        self._connection = connection

    @classmethod
    def connect(cls, host: str, port: int, timeout: float) -> 'SocketTransport':
        return cls(socket.create_connection((host, port), timeout=timeout))

    def send(self, data: bytes) -> None:
        self._connection.sendall(data)

    def receive(self, size: int, timeout: float) -> bytes:
        self._connection.settimeout(timeout)
        received = bytearray()
        while len(received) < size:
            chunk = self._connection.recv(size - len(received))
            if not chunk:
                raise ConnectionError('the broker closed the connection')

            received += chunk
        return bytes(received)

    def close(self) -> None:
        self._connection.close()


class MqttSession:
    def __init__(self, transport: Transport, config: Config) -> None:
        self._transport = transport
        self._config = config

    def connect(self) -> None:
        self._transport.send(connect_packet(self._config))
        code = connack_code(self._transport.receive(CONNACK_LENGTH, CONNECT_TIMEOUT))
        if code != 0:
            raise MqttRefusedError(code)

    def publish(self, topic: str, payload: bytes) -> None:
        self._transport.send(publish_packet(topic, payload))

    def ping(self) -> None:
        self._transport.send(PINGREQ)
        try:
            reply = self._transport.receive(len(PINGRESP), PING_TIMEOUT)
        except TimeoutError as error:
            raise MqttError(f'no PINGRESP within {PING_TIMEOUT:g} s') from error

        if reply != PINGRESP:
            raise MqttError(f'expected PINGRESP, got {reply!r}')

    def disconnect(self) -> None:
        try:
            self.publish(self._config.status_topic, OFFLINE)
            self._transport.send(DISCONNECT)
        finally:
            self._transport.close()

    def close(self) -> None:
        self._transport.close()


def idle_millis(now_ticks: int, last_input_ticks: int) -> int:
    return (now_ticks - last_input_ticks) & 0xFFFFFFFF


class IdleClock(Protocol):
    def idle_seconds(self) -> float: ...


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [('cbSize', ctypes.c_uint32), ('dwTime', ctypes.c_uint32)]


class WindowsIdleClock:
    def idle_seconds(self) -> float:
        if sys.platform != 'win32':
            raise OSError('GetLastInputInfo needs Windows')

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            raise ctypes.WinError()

        kernel32 = ctypes.windll.kernel32
        kernel32.GetTickCount.restype = ctypes.c_uint32
        return idle_millis(kernel32.GetTickCount(), info.dwTime) / 1000


class InstanceLock(Protocol):
    def acquire(self) -> bool: ...


class WindowsInstanceLock:
    def __init__(self, name: str) -> None:
        self._name = name
        self._handle: int | None = None

    def acquire(self) -> bool:
        if sys.platform != 'win32':
            raise OSError('a named mutex needs Windows')

        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(None, False, self._name)
        error = ctypes.get_last_error()
        if not handle and error == ERROR_ACCESS_DENIED:
            return False

        if not handle:
            raise ctypes.WinError(error)

        self._handle = handle
        return error != ERROR_ALREADY_EXISTS


class PresenceReporter:
    def __init__(self, interval: float = REPORT_INTERVAL) -> None:
        self._interval = interval
        self._reported_at: float | None = None
        self._reported: int | None = None

    def reset(self) -> None:
        self._reported_at = None
        self._reported = None

    def tick(self, now: float, idle: float) -> int | None:
        if self._reported_at is None:
            return self._report(now, 1 if idle <= self._interval else 0)

        since = now - self._reported_at
        presence = 1 if idle <= since else 0
        if since >= self._interval or (presence == 1 and self._reported == 0):
            return self._report(now, presence)

        return None

    def _report(self, now: float, presence: int) -> int:
        self._reported_at = now
        self._reported = presence
        return presence


class Backoff:
    def __init__(self, first: float = BACKOFF_FIRST, maximum: float = BACKOFF_MAX) -> None:
        self._first = first
        self._maximum = maximum
        self._next = first

    def next(self) -> float:
        delay = self._next
        self._next = min(delay * 2, self._maximum)
        return delay

    def reset(self) -> None:
        self._next = self._first


class PresenceClient:
    def __init__(
        self,
        config: Config,
        idle_clock: IdleClock,
        connect: Callable[[], Transport],
        *,
        reporter: PresenceReporter,
        backoff: Backoff,
        monotonic: Callable[[], float],
        sleep: Callable[[float], None],
        logger: logging.Logger,
    ) -> None:
        self._config = config
        self._idle_clock = idle_clock
        self._connect = connect
        self._reporter = reporter
        self._backoff = backoff
        self._monotonic = monotonic
        self._sleep = sleep
        self._logger = logger
        self._session: MqttSession | None = None
        self._shown: int | None = None

    @classmethod
    def init(
        cls,
        config: Config,
        idle_clock: IdleClock,
        connect: Callable[[], Transport],
        *,
        monotonic: Callable[[], float],
        sleep: Callable[[float], None],
        logger: logging.Logger,
    ) -> 'PresenceClient':
        return cls(
            config,
            idle_clock,
            connect,
            reporter=PresenceReporter(),
            backoff=Backoff(),
            monotonic=monotonic,
            sleep=sleep,
            logger=logger,
        )

    def serve(self) -> None:
        while True:
            session = self._connected()
            try:
                self._report(session)
            except (OSError, MqttError) as error:
                self._session = None
                session.close()
                self._logger.warning('connection lost: %s', error)
                self._sleep(self._backoff.next())

    def stop(self) -> None:
        if self._session is None:
            return

        try:
            self._session.disconnect()
        except OSError as error:
            self._logger.warning('offline not sent: %s', error)
            return

        self._logger.info('offline sent')

    def _connected(self) -> MqttSession:
        while True:
            try:
                session = self._open()
            except (OSError, MqttError) as error:
                self._logger.warning('connect failed: %s', error)
                self._sleep(self._backoff.next())
            else:
                self._backoff.reset()
                self._session = session
                return session

    def _open(self) -> MqttSession:
        session = MqttSession(self._connect(), self._config)
        try:
            session.connect()
        except (OSError, MqttError):
            session.close()
            raise

        self._logger.info(
            'connected to %s:%s as %s',
            self._config.host,
            self._config.port,
            self._config.username,
        )
        return session

    def _report(self, session: MqttSession) -> None:
        session.publish(self._config.status_topic, ONLINE)
        self._reporter.reset()
        pinged_at = self._monotonic()
        while True:
            now = self._monotonic()
            presence = self._reporter.tick(now, self._idle_clock.idle_seconds())
            if presence is not None:
                session.publish(self._config.presence_topic, str(presence).encode('ascii'))
                self._show(presence)
            if now - pinged_at >= KEEPALIVE:
                session.ping()
                pinged_at = now
            self._sleep(TICK)

    def _show(self, presence: int) -> None:
        if presence != self._shown:
            self._logger.info('presence %d', presence)
            self._shown = presence


def run(
    config: Config,
    idle_clock: IdleClock,
    connect: Callable[[], Transport],
    *,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
    logger: logging.Logger,
) -> None:
    client = PresenceClient.init(
        config,
        idle_clock,
        connect,
        monotonic=monotonic,
        sleep=sleep,
        logger=logger,
    )
    try:
        client.serve()
    except KeyboardInterrupt:
        client.stop()


def start(
    config_path: Path,
    *,
    lock: InstanceLock,
    idle_clock: IdleClock,
    connect: Callable[[str, int, float], Transport],
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
    logger: logging.Logger,
) -> int:
    if not lock.acquire():
        logger.error('another pc_presence is running in this session')
        return EXIT_RUNNING

    try:
        config = load_config(config_path)
    except ConfigError as error:
        logger.error('%s', error)
        return EXIT_CONFIG

    logger.info(
        'reporting %s to %s:%s as %s',
        config.presence_topic,
        config.host,
        config.port,
        config.username,
    )
    run(
        config,
        idle_clock,
        lambda: connect(config.host, config.port, CONNECT_TIMEOUT),
        monotonic=monotonic,
        sleep=sleep,
        logger=logger,
    )
    return 0


def main(argv: list[str]) -> int:
    config_path = Path(argv[0]) if argv else default_config_path()
    return start(
        config_path,
        lock=WindowsInstanceLock(MUTEX_NAME),
        idle_clock=WindowsIdleClock(),
        connect=SocketTransport.connect,
        monotonic=time.monotonic,
        sleep=time.sleep,
        logger=_logger(config_path.with_suffix('.log')),
    )


def _logger(path: Path) -> logging.Logger:
    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(path, maxBytes=LOG_BYTES, backupCount=1, encoding='utf-8'),
    ]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))
    logger = logging.getLogger('pc_presence')
    logger.setLevel(logging.INFO)
    for handler in handlers:
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        logger.addHandler(handler)
    return logger


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
