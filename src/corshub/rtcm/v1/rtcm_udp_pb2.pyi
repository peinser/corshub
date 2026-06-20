from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Datagram(_message.Message):
    __slots__ = ("version", "session_id", "seq", "hello", "hello_ack", "correction", "keepalive", "switch_mountpoint", "bye", "error")
    VERSION_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    SEQ_FIELD_NUMBER: _ClassVar[int]
    HELLO_FIELD_NUMBER: _ClassVar[int]
    HELLO_ACK_FIELD_NUMBER: _ClassVar[int]
    CORRECTION_FIELD_NUMBER: _ClassVar[int]
    KEEPALIVE_FIELD_NUMBER: _ClassVar[int]
    SWITCH_MOUNTPOINT_FIELD_NUMBER: _ClassVar[int]
    BYE_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    version: int
    session_id: int
    seq: int
    hello: Hello
    hello_ack: HelloAck
    correction: SignedCorrection
    keepalive: KeepAlive
    switch_mountpoint: SwitchMountpoint
    bye: Bye
    error: Error
    def __init__(self, version: _Optional[int] = ..., session_id: _Optional[int] = ..., seq: _Optional[int] = ..., hello: _Optional[_Union[Hello, _Mapping]] = ..., hello_ack: _Optional[_Union[HelloAck, _Mapping]] = ..., correction: _Optional[_Union[SignedCorrection, _Mapping]] = ..., keepalive: _Optional[_Union[KeepAlive, _Mapping]] = ..., switch_mountpoint: _Optional[_Union[SwitchMountpoint, _Mapping]] = ..., bye: _Optional[_Union[Bye, _Mapping]] = ..., error: _Optional[_Union[Error, _Mapping]] = ...) -> None: ...

class Hello(_message.Message):
    __slots__ = ("token", "mountpoint", "position", "client_version")
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    MOUNTPOINT_FIELD_NUMBER: _ClassVar[int]
    POSITION_FIELD_NUMBER: _ClassVar[int]
    CLIENT_VERSION_FIELD_NUMBER: _ClassVar[int]
    token: str
    mountpoint: str
    position: GgaPosition
    client_version: int
    def __init__(self, token: _Optional[str] = ..., mountpoint: _Optional[str] = ..., position: _Optional[_Union[GgaPosition, _Mapping]] = ..., client_version: _Optional[int] = ...) -> None: ...

class HelloAck(_message.Message):
    __slots__ = ("session_id", "mountpoint", "signing_kid", "keepalive_interval_s", "session_ttl_s")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    MOUNTPOINT_FIELD_NUMBER: _ClassVar[int]
    SIGNING_KID_FIELD_NUMBER: _ClassVar[int]
    KEEPALIVE_INTERVAL_S_FIELD_NUMBER: _ClassVar[int]
    SESSION_TTL_S_FIELD_NUMBER: _ClassVar[int]
    session_id: int
    mountpoint: str
    signing_kid: str
    keepalive_interval_s: int
    session_ttl_s: int
    def __init__(self, session_id: _Optional[int] = ..., mountpoint: _Optional[str] = ..., signing_kid: _Optional[str] = ..., keepalive_interval_s: _Optional[int] = ..., session_ttl_s: _Optional[int] = ...) -> None: ...

class KeepAlive(_message.Message):
    __slots__ = ("position",)
    POSITION_FIELD_NUMBER: _ClassVar[int]
    position: GgaPosition
    def __init__(self, position: _Optional[_Union[GgaPosition, _Mapping]] = ...) -> None: ...

class SwitchMountpoint(_message.Message):
    __slots__ = ("mountpoint", "position")
    MOUNTPOINT_FIELD_NUMBER: _ClassVar[int]
    POSITION_FIELD_NUMBER: _ClassVar[int]
    mountpoint: str
    position: GgaPosition
    def __init__(self, mountpoint: _Optional[str] = ..., position: _Optional[_Union[GgaPosition, _Mapping]] = ...) -> None: ...

class Bye(_message.Message):
    __slots__ = ("reason",)
    REASON_FIELD_NUMBER: _ClassVar[int]
    reason: str
    def __init__(self, reason: _Optional[str] = ...) -> None: ...

class Error(_message.Message):
    __slots__ = ("code", "message")
    CODE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    code: int
    message: str
    def __init__(self, code: _Optional[int] = ..., message: _Optional[str] = ...) -> None: ...

class SignedCorrection(_message.Message):
    __slots__ = ("payload", "signature")
    PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    SIGNATURE_FIELD_NUMBER: _ClassVar[int]
    payload: bytes
    signature: bytes
    def __init__(self, payload: _Optional[bytes] = ..., signature: _Optional[bytes] = ...) -> None: ...

class CorrectionFrame(_message.Message):
    __slots__ = ("timestamp_ms", "mountpoint", "rtcm")
    TIMESTAMP_MS_FIELD_NUMBER: _ClassVar[int]
    MOUNTPOINT_FIELD_NUMBER: _ClassVar[int]
    RTCM_FIELD_NUMBER: _ClassVar[int]
    timestamp_ms: int
    mountpoint: str
    rtcm: bytes
    def __init__(self, timestamp_ms: _Optional[int] = ..., mountpoint: _Optional[str] = ..., rtcm: _Optional[bytes] = ...) -> None: ...

class GgaPosition(_message.Message):
    __slots__ = ("latitude", "longitude")
    LATITUDE_FIELD_NUMBER: _ClassVar[int]
    LONGITUDE_FIELD_NUMBER: _ClassVar[int]
    latitude: float
    longitude: float
    def __init__(self, latitude: _Optional[float] = ..., longitude: _Optional[float] = ...) -> None: ...
