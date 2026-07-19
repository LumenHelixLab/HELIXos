"""Tests for src/BABEL/dispatcher.py (SPEC.md §3.5; AUD-C3/H5)."""

import pytest

from dispatcher import (
    MAX_COMMAND_BYTES,
    BabelDispatcher,
    CommandError,
    UnknownCommand,
    create_default_dispatcher,
)


class TestRegistration:
    def test_register_and_dispatch_roundtrip(self):
        d = BabelDispatcher(audit=lambda msg: None)
        seen = []
        d.register("foo", lambda args: seen.append(args) or {"ok": True})
        result = d.dispatch_direct("FOO alpha beta")
        assert result == {"ok": True}
        assert seen == [["alpha", "beta"]]  # verb routing: case-insensitive verb, args split

    def test_register_normalizes_case(self):
        d = BabelDispatcher()
        d.register("MiXeD", lambda args: "ok")
        assert d.dispatch_direct("mixed") == "ok"
        assert d.dispatch_direct("MIXED") == "ok"

    def test_register_rejects_invalid_verb(self):
        d = BabelDispatcher()
        for bad in ("", "   ", "HAS SPACE", "A\nB", None, 42):
            with pytest.raises(ValueError):
                d.register(bad, lambda args: None)

    def test_register_rejects_non_callable(self):
        d = BabelDispatcher()
        with pytest.raises(ValueError):
            d.register("X", "not-callable")

    def test_handler_exception_propagates(self):
        d = BabelDispatcher()

        def boom(args):
            raise RuntimeError("handler failed")

        d.register("BOOM", boom)
        with pytest.raises(RuntimeError, match="handler failed"):
            d.dispatch_direct("BOOM")


class TestValidation:
    def test_unknown_command(self):
        d = create_default_dispatcher()
        with pytest.raises(UnknownCommand, match="unknown command"):
            d.dispatch_direct("NOSUCHVERB")
        assert issubclass(UnknownCommand, CommandError)

    def test_non_string_rejected(self):
        d = create_default_dispatcher()
        for bad in (None, 123, b"PING", ["PING"]):
            with pytest.raises(CommandError, match="must be str"):
                d.dispatch_direct(bad)

    def test_empty_and_blank_rejected(self):
        d = create_default_dispatcher()
        for bad in ("", "   ", "\t"):
            with pytest.raises(CommandError, match="empty command"):
                d.dispatch_direct(bad)

    def test_crlf_nul_rejected(self):
        d = create_default_dispatcher()
        for bad in (
            "PING\nPRIVMSG #ops :hi",   # IRC line smuggling (AUD-H5)
            "PING\r\nMODE #x +o",
            "PING\x00ADMIN",
        ):
            with pytest.raises(CommandError, match="control characters"):
                d.dispatch_direct(bad)

    def test_oversize_rejected(self):
        d = create_default_dispatcher()
        too_long = "ECHO " + "A" * MAX_COMMAND_BYTES  # >400 utf-8 bytes
        assert len(too_long.encode("utf-8")) > MAX_COMMAND_BYTES
        with pytest.raises(CommandError, match="too long"):
            d.dispatch_direct(too_long)

    def test_oversize_multibyte_rejected(self):
        d = create_default_dispatcher()
        cmd = "ECHO " + "é" * 200  # 5 + 400 = 405 utf-8 bytes
        assert len(cmd) < MAX_COMMAND_BYTES  # char count under, byte count over
        with pytest.raises(CommandError, match="too long"):
            d.dispatch_direct(cmd)

    def test_exactly_max_bytes_accepted(self):
        d = create_default_dispatcher()
        cmd = "ECHO " + "A" * (MAX_COMMAND_BYTES - len("ECHO "))
        assert len(cmd.encode("utf-8")) == MAX_COMMAND_BYTES
        assert d.dispatch_direct(cmd) == cmd[len("ECHO "):]


class TestAudit:
    def test_audit_called_on_dispatch(self):
        records = []
        d = create_default_dispatcher(audit=records.append)
        d.dispatch_direct("PING")
        assert len(records) == 1
        assert records[0] == "babel.dispatch cmd='PING'"

    def test_audit_not_called_on_validation_failure_or_unknown(self):
        records = []
        d = create_default_dispatcher(audit=records.append)
        with pytest.raises(CommandError):
            d.dispatch_direct("PING\n")
        with pytest.raises(UnknownCommand):
            d.dispatch_direct("NOPE")
        assert records == []

    def test_default_audit_is_logging(self, caplog):
        d = create_default_dispatcher()  # no audit injected -> stdlib logging
        with caplog.at_level("INFO", logger="helix.babel"):
            d.dispatch_direct("PING")
        assert any("babel.dispatch cmd='PING'" in r.message for r in caplog.records)


class TestDefaultCommandSet:
    def test_echo(self):
        d = create_default_dispatcher()
        assert d.dispatch_direct("ECHO hello world") == "hello world"
        assert d.dispatch_direct("ECHO") == ""

    def test_ping(self):
        d = create_default_dispatcher()
        assert d.dispatch_direct("PING") == "PONG"

    def test_status(self):
        d = create_default_dispatcher()
        status = d.dispatch_direct("STATUS")
        assert status["status"] == "ok"
        assert status["registered_verbs"] == ["ECHO", "PING", "STATUS"]

    def test_verbs_property(self):
        d = create_default_dispatcher()
        assert d.verbs == ("ECHO", "PING", "STATUS")
