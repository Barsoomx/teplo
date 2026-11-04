import logging
import re
import socket
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from pc_presence import (
    CONNECT_TIMEOUT,
    DISCONNECT,
    EXIT_CONFIG,
    EXIT_RUNNING,
    PING_TIMEOUT,
    PINGREQ,
    PINGRESP,
    Backoff,
    Config,
    ConfigError,
    MqttError,
    MqttRefusedError,
    MqttSession,
    PresenceReporter,
    SocketTransport,
    WindowsIdleClock,
    WindowsInstanceLock,
    connack_code,
    connect_packet,
    default_config_path,
    idle_millis,
    load_config,
    publish_packet,
    remaining_length,
    run,
    start,
)

CONNACK_ACCEPTED = b'\x20\x02\x00\x00'
STATUS_TOPIC = 'teplo/teplo-pc-office/status'
PRESENCE_TOPIC = 'teplo/teplo-pc-office/sensor/presence/state'
FULL_TOML = """
host = ' 192.168.1.1 '
port = 1884
username = ' pc-office '
password = ' pc password '
node = 'teplo-pc-den'
"""
MINIMAL_TOML = """
host = '192.168.1.1'
username = 'pc-office'
password = 'pc-password'
"""
ROUTER_CONFIG = Config(
    host='192.168.1.1',
    port=1883,
    username='pc-office',
    password='pc-password',
    node='teplo-pc-office',
)


class SleepLimitError(Exception):
    pass


class TransportStub:
    def __init__(self, replies: bytes = b'', sends: int | None = None) -> None:
        self._replies = bytearray(replies)
        self._sends = sends
        self._sent: list[bytes] = []
        self._timeouts: list[float] = []
        self._closed = False

    @property
    def sent(self) -> list[bytes]:
        return self._sent

    @property
    def timeouts(self) -> list[float]:
        return self._timeouts

    @property
    def closed(self) -> bool:
        return self._closed

    def send(self, data: bytes) -> None:
        if self._sends is not None and len(self._sent) == self._sends:
            raise ConnectionResetError('connection reset by peer')

        self._sent.append(data)

    def receive(self, size: int, timeout: float) -> bytes:
        self._timeouts.append(timeout)
        if len(self._replies) < size:
            raise TimeoutError('timed out')

        reply = bytes(self._replies[:size])
        del self._replies[:size]
        return reply

    def close(self) -> None:
        self._closed = True


class ConnectStub:
    def __init__(self, outcomes: list[TransportStub | OSError]) -> None:
        self._outcomes = outcomes
        self._calls: list[tuple[str, int, float]] = []

    @property
    def calls(self) -> list[tuple[str, int, float]]:
        return self._calls

    def __call__(self, host: str, port: int, timeout: float) -> TransportStub:
        self._calls.append((host, port, timeout))
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, OSError):
            raise outcome

        return outcome


class IdleClockStub:
    def __init__(self, idle: float) -> None:
        self._idle = idle

    def idle_seconds(self) -> float:
        return self._idle


class InstanceLockStub:
    def __init__(self, acquired: bool) -> None:
        self._acquired = acquired

    def acquire(self) -> bool:
        return self._acquired


class TimeStub:
    def __init__(self, now: float, sleeps: int, stop: type[BaseException] = SleepLimitError) -> None:
        self._now = now
        self._sleeps = sleeps
        self._stop = stop
        self._slept: list[float] = []

    @property
    def slept(self) -> list[float]:
        return self._slept

    def monotonic(self) -> float:
        return self._now

    def sleep(self, seconds: float) -> None:
        self._slept.append(seconds)
        if len(self._slept) > self._sleeps:
            raise self._stop

        self._now += seconds


class LogRecorder(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self._messages: list[str] = []

    @property
    def messages(self) -> list[str]:
        return self._messages

    def emit(self, record: logging.LogRecord) -> None:
        self._messages.append(record.getMessage())


@pytest.fixture
def f_config() -> Config:
    return Config(
        host='broker.invalid',
        port=1883,
        username='pc-office',
        password='pc-password',
        node='teplo-pc-office',
    )


@pytest.fixture
def f_config_path(tmp_path: Path, request: pytest.FixtureRequest) -> Path:
    path = tmp_path / 'pc_presence.toml'
    path.write_text(request.param, encoding='utf-8')
    return path


@pytest.fixture
def f_bom_config_path(tmp_path: Path) -> Path:
    path = tmp_path / 'pc_presence.toml'
    path.write_bytes(b'\xef\xbb\xbf' + MINIMAL_TOML.encode('utf-8'))
    return path


@pytest.fixture
def f_missing_config_path(tmp_path: Path) -> Path:
    return tmp_path / 'pc_presence.toml'


@pytest.fixture
def f_log() -> LogRecorder:
    return LogRecorder()


@pytest.fixture
def f_logger(f_log: LogRecorder) -> logging.Logger:
    logger = logging.Logger('pc_presence_tests')
    logger.addHandler(f_log)
    return logger


@pytest.fixture
def f_transport() -> TransportStub:
    return TransportStub(replies=CONNACK_ACCEPTED)


@pytest.fixture
def f_pinging_transport() -> TransportStub:
    return TransportStub(replies=CONNACK_ACCEPTED + PINGRESP)


@pytest.fixture
def f_refusing_transport() -> TransportStub:
    return TransportStub(replies=b'\x20\x02\x00\x04')


@pytest.fixture
def f_lossy_transport() -> TransportStub:
    return TransportStub(replies=CONNACK_ACCEPTED, sends=2)


@pytest.fixture
def f_session(f_transport: TransportStub, f_config: Config) -> MqttSession:
    return MqttSession(f_transport, f_config)


@pytest.fixture
def f_connect(f_transport: TransportStub) -> ConnectStub:
    return ConnectStub([f_transport])


@pytest.fixture
def f_refused_connect(f_refusing_transport: TransportStub) -> ConnectStub:
    return ConnectStub([f_refusing_transport])


@pytest.fixture
def f_unreachable_connect() -> ConnectStub:
    return ConnectStub([ConnectionRefusedError('refused')])


@pytest.fixture
def f_flaky_connect(f_lossy_transport: TransportStub, f_transport: TransportStub) -> ConnectStub:
    return ConnectStub(
        [
            ConnectionRefusedError('refused'),
            ConnectionRefusedError('refused'),
            ConnectionRefusedError('refused'),
            f_lossy_transport,
            f_transport,
        ],
    )


@pytest.fixture
def f_idle_clock(request: pytest.FixtureRequest) -> IdleClockStub:
    return IdleClockStub(idle=getattr(request, 'param', 3.0))


@pytest.fixture
def f_first_tick() -> TimeStub:
    return TimeStub(now=1000.0, sleeps=0)


@pytest.fixture
def f_minute() -> TimeStub:
    return TimeStub(now=1000.0, sleeps=60)


@pytest.fixture
def f_retries() -> TimeStub:
    return TimeStub(now=1000.0, sleeps=4)


@pytest.fixture
def f_interrupt() -> TimeStub:
    return TimeStub(now=1000.0, sleeps=0, stop=KeyboardInterrupt)


@pytest.fixture
def f_free_lock() -> InstanceLockStub:
    return InstanceLockStub(acquired=True)


@pytest.fixture
def f_taken_lock() -> InstanceLockStub:
    return InstanceLockStub(acquired=False)


@pytest.fixture
def f_reporter() -> PresenceReporter:
    return PresenceReporter()


@pytest.fixture
def f_backoff() -> Backoff:
    return Backoff()


@pytest.fixture
def socket_pair() -> Iterator[tuple[socket.socket, socket.socket]]:
    ours, brokers = socket.socketpair()
    yield ours, brokers
    ours.close()
    brokers.close()


@pytest.fixture
def f_socket_transport(socket_pair: tuple[socket.socket, socket.socket]) -> SocketTransport:
    return SocketTransport(socket_pair[0])


@pytest.fixture
def f_broker(socket_pair: tuple[socket.socket, socket.socket]) -> socket.socket:
    return socket_pair[1]


@pytest.mark.parametrize(
    ('length', 'encoded'),
    [
        (0, b'\x00'),
        (127, b'\x7f'),
        (128, b'\x80\x01'),
        (16383, b'\xff\x7f'),
        (16384, b'\x80\x80\x01'),
        (2097151, b'\xff\xff\x7f'),
        (268435455, b'\xff\xff\xff\x7f'),
    ],
)
def test_remaining_length_is_mqtt_varint(length: int, encoded: bytes) -> None:
    assert remaining_length(length) == encoded


@pytest.mark.parametrize('length', [-1, 268435456])
def test_remaining_length_refuses_a_length_mqtt_cannot_carry(length: int) -> None:
    with pytest.raises(ValueError, match='an MQTT packet carries 0 to 268435455 bytes'):
        remaining_length(length)


def test_connect_packet_carries_the_will_user_and_password(f_config: Config) -> None:
    assert connect_packet(f_config) == (
        b'\x10\x5a'
        b'\x00\x04MQTT\x04\xc6\x00\x3c'
        b'\x00\x0fteplo-pc-office'
        b'\x00\x1cteplo/teplo-pc-office/status'
        b'\x00\x07offline'
        b'\x00\x09pc-office'
        b'\x00\x0bpc-password'
    )


def test_publish_packet_is_qos_0_and_not_retained() -> None:
    assert publish_packet(PRESENCE_TOPIC, b'1') == b'\x30\x2e\x00\x2bteplo/teplo-pc-office/sensor/presence/state1'


def test_ping_and_disconnect_packets() -> None:
    assert (PINGREQ, PINGRESP, DISCONNECT) == (b'\xc0\x00', b'\xd0\x00', b'\xe0\x00')


@pytest.mark.parametrize(
    ('packet', 'code'),
    [
        (b'\x20\x02\x00\x00', 0),
        (b'\x20\x02\x01\x00', 0),
        (b'\x20\x02\x00\x04', 4),
        (b'\x20\x02\x00\x05', 5),
    ],
    ids=['accepted', 'accepted_with_a_session', 'bad_user_name_or_password', 'not_authorized'],
)
def test_connack_code_reads_the_return_code(packet: bytes, code: int) -> None:
    assert connack_code(packet) == code


@pytest.mark.parametrize('packet', [b'\x20\x03\x00\x00', b'\x30\x02\x00\x00', b'\x20\x02\x00'])
def test_connack_code_refuses_another_packet(packet: bytes) -> None:
    with pytest.raises(MqttError, match='expected CONNACK'):
        connack_code(packet)


@pytest.mark.parametrize(
    ('code', 'meaning'),
    [(4, 'bad user name or password'), (5, 'not authorized'), (9, 'refused with code 9')],
)
def test_refusal_names_its_meaning(code: int, meaning: str) -> None:
    assert str(MqttRefusedError(code)) == meaning


@pytest.mark.parametrize(
    ('now_ticks', 'last_input_ticks', 'millis'),
    [(1500, 1000, 500), (5, 0xFFFFFFF0, 21), (0, 0, 0)],
    ids=['plain', 'across_the_wrap', 'input_now'],
)
def test_idle_millis_counts_across_the_tick_count_wrap(now_ticks: int, last_input_ticks: int, millis: int) -> None:
    assert idle_millis(now_ticks, last_input_ticks) == millis


@pytest.mark.parametrize(
    ('ticks', 'reports'),
    [
        ([(100.0, 5.0)], [1]),
        ([(100.0, 45.0)], [0]),
        ([(100.0, 0.0), (129.0, 0.0), (130.0, 0.0)], [1, None, 1]),
        ([(100.0, 0.0), (130.0, 31.0)], [1, 0]),
        ([(100.0, 40.0), (110.0, 11.0), (130.0, 31.0)], [0, None, 0]),
        ([(100.0, 40.0), (110.0, 2.0), (111.0, 0.0)], [0, 1, None]),
        ([(100.0, 0.0), (105.0, 0.0)], [1, None]),
    ],
    ids=[
        'first_tick_with_recent_input',
        'first_tick_without_input',
        'next_report_after_30_seconds',
        'no_input_since_the_previous_report',
        'still_no_input',
        'input_after_a_0_at_once',
        'no_early_report_while_at_1',
    ],
)
def test_presence_reporter_reports_input_since_its_previous_report(
    f_reporter: PresenceReporter,
    ticks: list[tuple[float, float]],
    reports: list[int | None],
) -> None:
    assert [f_reporter.tick(now, idle) for now, idle in ticks] == reports


def test_presence_reporter_reports_at_once_after_reset(f_reporter: PresenceReporter) -> None:
    first = f_reporter.tick(100.0, 0.0)
    f_reporter.reset()

    assert (first, f_reporter.tick(101.0, 41.0)) == (1, 0)


def test_backoff_doubles_up_to_30_seconds_and_starts_over_on_reset(f_backoff: Backoff) -> None:
    delays = [f_backoff.next() for _ in range(7)]
    f_backoff.reset()

    assert (delays, f_backoff.next()) == ([1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0], 1.0)


@pytest.mark.parametrize('f_config_path', [FULL_TOML], indirect=True)
def test_load_config_reads_every_field(f_config_path: Path) -> None:
    assert load_config(f_config_path) == Config(
        host='192.168.1.1',
        port=1884,
        username='pc-office',
        password=' pc password ',
        node='teplo-pc-den',
    )


@pytest.mark.parametrize('f_config_path', [MINIMAL_TOML], indirect=True)
def test_load_config_defaults_port_and_node(f_config_path: Path) -> None:
    assert load_config(f_config_path) == ROUTER_CONFIG


def test_load_config_reads_a_file_saved_with_a_byte_order_mark(f_bom_config_path: Path) -> None:
    assert load_config(f_bom_config_path) == ROUTER_CONFIG


@pytest.mark.parametrize(
    ('f_config_path', 'error'),
    [
        (MINIMAL_TOML.replace("password = 'pc-password'\n", ''), 'password must be set'),
        (MINIMAL_TOML.replace("password = 'pc-password'", "password = ''"), 'password must be set'),
        (MINIMAL_TOML.replace("host = '192.168.1.1'", "host = ' '"), 'host must be set'),
        (MINIMAL_TOML.replace("username = 'pc-office'", 'username = 7'), 'username must be set'),
        (f'{MINIMAL_TOML}port = 0\n', 'port must be a whole number from 1 to 65535, not 0'),
        (f'{MINIMAL_TOML}port = 70000\n', 'port must be a whole number from 1 to 65535, not 70000'),
        (f'{MINIMAL_TOML}port = true\n', 'port must be a whole number from 1 to 65535, not True'),
        (f"{MINIMAL_TOML}port = '1883'\n", "port must be a whole number from 1 to 65535, not '1883'"),
        (
            f"{MINIMAL_TOML}node = 'pc-office'\n",
            "node must be teplo- and lowercase letters, digits or hyphens, not 'pc-office'",
        ),
        (
            f"{MINIMAL_TOML}node = 'teplo-PC'\n",
            "node must be teplo- and lowercase letters, digits or hyphens, not 'teplo-PC'",
        ),
        (f'{MINIMAL_TOML}node\n', 'is not valid TOML'),
    ],
    ids=[
        'no_password',
        'empty_password',
        'blank_host',
        'username_not_text',
        'port_0',
        'port_70000',
        'port_not_a_number',
        'port_as_text',
        'node_outside_teplo',
        'node_in_capitals',
        'broken_toml',
    ],
    indirect=['f_config_path'],
)
def test_load_config_refuses_a_wrong_config(f_config_path: Path, error: str) -> None:
    with pytest.raises(ConfigError, match=re.escape(error)):
        load_config(f_config_path)


def test_load_config_refuses_a_missing_file(f_missing_config_path: Path) -> None:
    with pytest.raises(ConfigError, match='does not exist: copy pc_presence.example.toml to it'):
        load_config(f_missing_config_path)


@pytest.mark.parametrize('f_config_path', [f'{MINIMAL_TOML}port = 0\n'], indirect=True)
def test_config_error_leaves_the_password_out(f_config_path: Path) -> None:
    with pytest.raises(ConfigError) as error:
        load_config(f_config_path)

    assert 'pc-password' not in str(error.value)


def test_config_repr_leaves_the_password_out(f_config: Config) -> None:
    assert 'pc-password' not in repr(f_config)


def test_config_names_the_node_topics(f_config: Config) -> None:
    assert (f_config.status_topic, f_config.presence_topic) == (STATUS_TOPIC, PRESENCE_TOPIC)


def test_default_config_sits_beside_the_script() -> None:
    assert default_config_path() == Path(__file__).with_name('pc_presence.toml')


def test_session_connect_sends_connect_and_accepts_connack(
    f_session: MqttSession,
    f_transport: TransportStub,
    f_config: Config,
) -> None:
    f_session.connect()

    assert (f_transport.sent, f_transport.timeouts) == ([connect_packet(f_config)], [CONNECT_TIMEOUT])


def test_session_connect_raises_the_refusal_with_its_code(
    f_refusing_transport: TransportStub,
    f_config: Config,
) -> None:
    session = MqttSession(f_refusing_transport, f_config)

    with pytest.raises(MqttRefusedError) as error:
        session.connect()

    assert (error.value.code, str(error.value)) == (4, 'bad user name or password')


def test_session_publish_sends_the_packet(f_session: MqttSession, f_transport: TransportStub) -> None:
    f_session.publish(PRESENCE_TOPIC, b'0')

    assert f_transport.sent == [publish_packet(PRESENCE_TOPIC, b'0')]


def test_session_ping_accepts_pingresp(f_pinging_transport: TransportStub, f_config: Config) -> None:
    session = MqttSession(f_pinging_transport, f_config)
    session.connect()

    session.ping()

    assert (f_pinging_transport.sent[1:], f_pinging_transport.timeouts) == (
        [PINGREQ],
        [CONNECT_TIMEOUT, PING_TIMEOUT],
    )


def test_session_ping_raises_without_pingresp(f_session: MqttSession) -> None:
    f_session.connect()

    with pytest.raises(MqttError, match='no PINGRESP within 10 s'):
        f_session.ping()


def test_session_ping_raises_on_another_reply(f_refusing_transport: TransportStub, f_config: Config) -> None:
    session = MqttSession(f_refusing_transport, f_config)

    with pytest.raises(MqttError, match='expected PINGRESP'):
        session.ping()


def test_session_disconnect_publishes_offline_then_disconnects_and_closes(
    f_session: MqttSession,
    f_transport: TransportStub,
) -> None:
    f_session.disconnect()

    assert (f_transport.sent, f_transport.closed) == (
        [publish_packet(STATUS_TOPIC, b'offline'), DISCONNECT],
        True,
    )


def test_session_disconnect_closes_when_the_connection_is_gone(
    f_lossy_transport: TransportStub,
    f_config: Config,
) -> None:
    session = MqttSession(f_lossy_transport, f_config)
    session.connect()
    session.publish(STATUS_TOPIC, b'online')

    with pytest.raises(ConnectionResetError):
        session.disconnect()

    assert f_lossy_transport.closed


def test_socket_transport_sends_and_receives_exactly_the_size_asked_for(
    f_socket_transport: SocketTransport,
    f_broker: socket.socket,
) -> None:
    f_socket_transport.send(PINGREQ)
    f_broker.sendall(b'\x20\x02')
    f_broker.sendall(b'\x00\x00\xd0\x00')

    assert (f_broker.recv(16), f_socket_transport.receive(4, 1.0), f_socket_transport.receive(2, 1.0)) == (
        PINGREQ,
        CONNACK_ACCEPTED,
        PINGRESP,
    )


def test_socket_transport_raises_when_the_broker_closes(
    f_socket_transport: SocketTransport,
    f_broker: socket.socket,
) -> None:
    f_broker.sendall(b'\xd0')
    f_broker.shutdown(socket.SHUT_WR)

    with pytest.raises(ConnectionError, match='the broker closed the connection'):
        f_socket_transport.receive(2, 1.0)


def test_socket_transport_times_out_on_silence(f_socket_transport: SocketTransport) -> None:
    with pytest.raises(TimeoutError):
        f_socket_transport.receive(2, 0.05)


@pytest.mark.parametrize(
    ('f_idle_clock', 'presence'),
    [(3.0, b'1'), (45.0, b'0')],
    ids=['input', 'no_input'],
    indirect=['f_idle_clock'],
)
def test_run_publishes_online_then_presence_at_once(
    f_config: Config,
    f_idle_clock: IdleClockStub,
    f_transport: TransportStub,
    f_first_tick: TimeStub,
    f_logger: logging.Logger,
    presence: bytes,
) -> None:
    with pytest.raises(SleepLimitError):
        run(
            f_config,
            f_idle_clock,
            lambda: f_transport,
            monotonic=f_first_tick.monotonic,
            sleep=f_first_tick.sleep,
            logger=f_logger,
        )

    assert (f_transport.sent, f_first_tick.slept) == (
        [
            connect_packet(f_config),
            publish_packet(STATUS_TOPIC, b'online'),
            publish_packet(PRESENCE_TOPIC, presence),
        ],
        [1.0],
    )


def test_run_reports_every_30_seconds_and_pings_every_minute(
    f_config: Config,
    f_idle_clock: IdleClockStub,
    f_pinging_transport: TransportStub,
    f_minute: TimeStub,
    f_logger: logging.Logger,
    f_log: LogRecorder,
) -> None:
    with pytest.raises(SleepLimitError):
        run(
            f_config,
            f_idle_clock,
            lambda: f_pinging_transport,
            monotonic=f_minute.monotonic,
            sleep=f_minute.sleep,
            logger=f_logger,
        )

    assert (f_pinging_transport.sent, f_pinging_transport.timeouts, f_log.messages) == (
        [
            connect_packet(f_config),
            publish_packet(STATUS_TOPIC, b'online'),
            publish_packet(PRESENCE_TOPIC, b'1'),
            publish_packet(PRESENCE_TOPIC, b'1'),
            publish_packet(PRESENCE_TOPIC, b'1'),
            PINGREQ,
        ],
        [CONNECT_TIMEOUT, PING_TIMEOUT],
        ['connected to broker.invalid:1883 as pc-office', 'presence 1'],
    )


def test_run_reconnects_with_the_backoff_after_failed_connects_and_a_lost_connection(
    f_config: Config,
    f_idle_clock: IdleClockStub,
    f_flaky_connect: ConnectStub,
    f_lossy_transport: TransportStub,
    f_transport: TransportStub,
    f_retries: TimeStub,
    f_logger: logging.Logger,
    f_log: LogRecorder,
) -> None:
    with pytest.raises(SleepLimitError):
        run(
            f_config,
            f_idle_clock,
            lambda: f_flaky_connect(f_config.host, f_config.port, CONNECT_TIMEOUT),
            monotonic=f_retries.monotonic,
            sleep=f_retries.sleep,
            logger=f_logger,
        )

    assert (f_retries.slept, f_lossy_transport.closed, f_transport.sent, f_log.messages) == (
        [1.0, 2.0, 4.0, 1.0, 1.0],
        True,
        [
            connect_packet(f_config),
            publish_packet(STATUS_TOPIC, b'online'),
            publish_packet(PRESENCE_TOPIC, b'1'),
        ],
        [
            'connect failed: refused',
            'connect failed: refused',
            'connect failed: refused',
            'connected to broker.invalid:1883 as pc-office',
            'connection lost: connection reset by peer',
            'connected to broker.invalid:1883 as pc-office',
            'presence 1',
        ],
    )


def test_run_reconnects_when_the_broker_does_not_answer_a_ping(
    f_config: Config,
    f_idle_clock: IdleClockStub,
    f_transport: TransportStub,
    f_minute: TimeStub,
    f_logger: logging.Logger,
    f_log: LogRecorder,
) -> None:
    with pytest.raises(SleepLimitError):
        run(
            f_config,
            f_idle_clock,
            lambda: f_transport,
            monotonic=f_minute.monotonic,
            sleep=f_minute.sleep,
            logger=f_logger,
        )

    assert (f_transport.closed, f_minute.slept[-1], f_log.messages[-1]) == (
        True,
        1.0,
        'connection lost: no PINGRESP within 10 s',
    )


def test_run_logs_what_a_refusal_means_and_closes_its_connection(
    f_config: Config,
    f_idle_clock: IdleClockStub,
    f_refused_connect: ConnectStub,
    f_refusing_transport: TransportStub,
    f_first_tick: TimeStub,
    f_logger: logging.Logger,
    f_log: LogRecorder,
) -> None:
    with pytest.raises(SleepLimitError):
        run(
            f_config,
            f_idle_clock,
            lambda: f_refused_connect(f_config.host, f_config.port, CONNECT_TIMEOUT),
            monotonic=f_first_tick.monotonic,
            sleep=f_first_tick.sleep,
            logger=f_logger,
        )

    assert (f_refusing_transport.closed, f_first_tick.slept, f_log.messages) == (
        True,
        [1.0],
        ['connect failed: bad user name or password'],
    )


def test_run_sends_offline_when_interrupted(
    f_config: Config,
    f_idle_clock: IdleClockStub,
    f_transport: TransportStub,
    f_interrupt: TimeStub,
    f_logger: logging.Logger,
    f_log: LogRecorder,
) -> None:
    run(
        f_config,
        f_idle_clock,
        lambda: f_transport,
        monotonic=f_interrupt.monotonic,
        sleep=f_interrupt.sleep,
        logger=f_logger,
    )

    assert (f_transport.sent[-2:], f_transport.closed, f_log.messages[-1]) == (
        [publish_packet(STATUS_TOPIC, b'offline'), DISCONNECT],
        True,
        'offline sent',
    )


def test_run_returns_when_interrupted_while_it_waits_to_reconnect(
    f_config: Config,
    f_idle_clock: IdleClockStub,
    f_unreachable_connect: ConnectStub,
    f_interrupt: TimeStub,
    f_logger: logging.Logger,
    f_log: LogRecorder,
) -> None:
    run(
        f_config,
        f_idle_clock,
        lambda: f_unreachable_connect(f_config.host, f_config.port, CONNECT_TIMEOUT),
        monotonic=f_interrupt.monotonic,
        sleep=f_interrupt.sleep,
        logger=f_logger,
    )

    assert (f_interrupt.slept, f_log.messages) == ([1.0], ['connect failed: refused'])


@pytest.mark.parametrize('f_config_path', [MINIMAL_TOML], indirect=True)
def test_start_exits_at_once_without_connecting_while_another_copy_runs(
    f_config_path: Path,
    f_taken_lock: InstanceLockStub,
    f_idle_clock: IdleClockStub,
    f_connect: ConnectStub,
    f_first_tick: TimeStub,
    f_logger: logging.Logger,
    f_log: LogRecorder,
) -> None:
    code = start(
        f_config_path,
        lock=f_taken_lock,
        idle_clock=f_idle_clock,
        connect=f_connect,
        monotonic=f_first_tick.monotonic,
        sleep=f_first_tick.sleep,
        logger=f_logger,
    )

    assert (code, f_connect.calls, f_log.messages) == (
        EXIT_RUNNING,
        [],
        ['another pc_presence is running in this session'],
    )


def test_start_exits_on_a_config_error(
    f_missing_config_path: Path,
    f_free_lock: InstanceLockStub,
    f_idle_clock: IdleClockStub,
    f_connect: ConnectStub,
    f_first_tick: TimeStub,
    f_logger: logging.Logger,
    f_log: LogRecorder,
) -> None:
    code = start(
        f_missing_config_path,
        lock=f_free_lock,
        idle_clock=f_idle_clock,
        connect=f_connect,
        monotonic=f_first_tick.monotonic,
        sleep=f_first_tick.sleep,
        logger=f_logger,
    )

    assert (code, f_connect.calls, f_log.messages) == (
        EXIT_CONFIG,
        [],
        [f'{f_missing_config_path} does not exist: copy pc_presence.example.toml to it and set the password'],
    )


@pytest.mark.parametrize('f_config_path', [MINIMAL_TOML], indirect=True)
def test_start_runs_until_interrupted_while_the_lock_is_free(
    f_config_path: Path,
    f_free_lock: InstanceLockStub,
    f_idle_clock: IdleClockStub,
    f_connect: ConnectStub,
    f_transport: TransportStub,
    f_interrupt: TimeStub,
    f_logger: logging.Logger,
) -> None:
    code = start(
        f_config_path,
        lock=f_free_lock,
        idle_clock=f_idle_clock,
        connect=f_connect,
        monotonic=f_interrupt.monotonic,
        sleep=f_interrupt.sleep,
        logger=f_logger,
    )

    assert (code, f_connect.calls, f_transport.sent) == (
        0,
        [('192.168.1.1', 1883, CONNECT_TIMEOUT)],
        [
            connect_packet(ROUTER_CONFIG),
            publish_packet(STATUS_TOPIC, b'online'),
            publish_packet(PRESENCE_TOPIC, b'1'),
            publish_packet(STATUS_TOPIC, b'offline'),
            DISCONNECT,
        ],
    )


@pytest.mark.skipif(sys.platform == 'win32', reason='checks the guard off Windows')
def test_windows_idle_clock_needs_windows() -> None:
    with pytest.raises(OSError, match='GetLastInputInfo needs Windows'):
        WindowsIdleClock().idle_seconds()


@pytest.mark.skipif(sys.platform == 'win32', reason='checks the guard off Windows')
def test_windows_instance_lock_needs_windows() -> None:
    with pytest.raises(OSError, match='a named mutex needs Windows'):
        WindowsInstanceLock('Local\\teplo-pc-presence-test').acquire()
