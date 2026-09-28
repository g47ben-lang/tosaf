"""build_ydl_opts wiring for the anti-bot-detection knobs (proxy / impersonate
/ player_clients) — no network involved, just checking the opts dict yt-dlp
will receive.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yt_dlp.networking.impersonate import ImpersonateTarget

from savebridge_server.ytdlp_opts import build_ydl_opts


def _base_opts(**overrides):
    kwargs = dict(
        fmt="video",
        quality="best",
        audio_lang=None,
        sub_lang=None,
        outtmpl="/tmp/x/%(title)s.%(ext)s",
        cookiefile=None,
        progress_hook=lambda d: None,
    )
    kwargs.update(overrides)
    return build_ydl_opts(**kwargs)


def test_no_knobs_by_default():
    opts = _base_opts()
    assert "proxy" not in opts
    assert "impersonate" not in opts
    assert "extractor_args" not in opts
    print("OK: no anti-bot knobs set when not requested")


def test_proxy_passthrough():
    opts = _base_opts(proxy="http://user:pass@127.0.0.1:9999")
    assert opts["proxy"] == "http://user:pass@127.0.0.1:9999"
    print("OK: proxy passed through")


def test_impersonate_target_resolved():
    opts = _base_opts(impersonate="chrome")
    assert isinstance(opts["impersonate"], ImpersonateTarget)
    print("OK: impersonate string resolved to ImpersonateTarget")


def test_player_clients_extractor_args():
    opts = _base_opts(player_clients=["web", "ios", "android"])
    assert opts["extractor_args"] == {"youtube": {"player_client": ["web", "ios", "android"]}}
    print("OK: player_clients wired into youtube extractor_args")


if __name__ == "__main__":
    for fn in (
        test_no_knobs_by_default,
        test_proxy_passthrough,
        test_impersonate_target_resolved,
        test_player_clients_extractor_args,
    ):
        fn()
    print("ALL YTDLP OPTS TESTS PASSED")
