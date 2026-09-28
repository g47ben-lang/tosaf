"""Map the extension's (format, quality, audioLang, subLang) into yt-dlp options.

The extension sends these presets (see the original extension code):

  video_best  -> format=video      quality=best
  video_1080  -> format=video      quality=1080
  video_720   -> format=video      quality=720
  video_480   -> format=video      quality=480
  audio_only  -> format=audio      quality=audio_best
  low_phone   -> format=low_phone  quality=480
  + optional audioLang (e.g. "he") and subLang (e.g. "he").
"""

from __future__ import annotations

from typing import Optional, Sequence

from yt_dlp.networking.impersonate import ImpersonateTarget


def _height_cap(quality: str) -> Optional[int]:
    try:
        return int(quality)
    except (TypeError, ValueError):
        return None


def _video_format_selector(quality: str, audio_lang: Optional[str]) -> str:
    cap = _height_cap(quality)
    height = f"[height<={cap}]" if cap else ""

    # Prefer an audio track in the requested language when one is asked for,
    # but always fall back so a video without that track still downloads.
    if audio_lang:
        return (
            f"bv*{height}+ba[language={audio_lang}]/"
            f"bv*{height}+ba/"
            f"b{height}/b"
        )
    return f"bv*{height}+ba/b{height}/b"


def build_ydl_opts(
    *,
    fmt: str,
    quality: str,
    audio_lang: Optional[str],
    sub_lang: Optional[str],
    outtmpl: str,
    cookiefile: Optional[str],
    progress_hook,
    proxy: Optional[str] = None,
    impersonate: Optional[str] = None,
    player_clients: Optional[Sequence[str]] = None,
) -> dict:
    """Return an options dict for yt_dlp.YoutubeDL(...).

    proxy / impersonate / player_clients are the anti-bot-detection knobs:
    a datacenter IP alone gets flagged by YouTube regardless of cookies, so
    `proxy` should point at a residential/mobile proxy; `impersonate` makes
    the TLS/HTTP2 handshake match a real browser (needs curl_cffi); and
    `player_clients` picks which YouTube API clients to try (mobile clients
    are less dependent on a full Botguard PO Token than the web client).
    """
    opts: dict = {
        "outtmpl": outtmpl,
        "noprogress": True,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "restrictfilenames": False,
        "progress_hooks": [progress_hook],
        # keep everything self-contained under the job dir
        "paths": {},
    }
    if cookiefile:
        opts["cookiefile"] = cookiefile

    if proxy:
        opts["proxy"] = proxy

    if impersonate:
        target = ImpersonateTarget.from_str(impersonate)
        if target:
            opts["impersonate"] = target

    if player_clients:
        opts["extractor_args"] = {"youtube": {"player_client": list(player_clients)}}

    postprocessors: list[dict] = []

    if fmt == "audio":
        # Audio-only -> MP3 with embedded cover art + metadata (the "MP3 with
        # image" the extension advertises).
        opts["format"] = "ba/b"
        opts["writethumbnail"] = True
        postprocessors.append(
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"}
        )
        postprocessors.append({"key": "FFmpegMetadata", "add_metadata": True})
        postprocessors.append({"key": "EmbedThumbnail"})

    elif fmt == "low_phone":
        # Small phone-friendly clip, recoded to 3gp.
        opts["format"] = "worst[ext=mp4]/worst"
        opts["merge_output_format"] = "mp4"
        postprocessors.append({"key": "FFmpegVideoConvertor", "preferedformat": "3gp"})

    else:  # "video"
        opts["format"] = _video_format_selector(quality, audio_lang)
        opts["merge_output_format"] = "mp4"

    if sub_lang:
        opts["writesubtitles"] = True
        opts["subtitleslangs"] = [sub_lang]
        # embed into the container so the single output file carries them
        postprocessors.append({"key": "FFmpegEmbedSubtitle"})

    if postprocessors:
        opts["postprocessors"] = postprocessors

    return opts
