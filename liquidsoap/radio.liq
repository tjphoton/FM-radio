# Blue Hour Radio — Liquidsoap Main Script
# Liquidsoap 2.x
#
# Architecture:
#   music_library     → watchfolder, auto-picks up new MP3s
#   dj_segments       → watchfolder, matched to tracks by filename prefix
#   station_ids       → playlist, inserted every ~25 min
#   shows_library     → playlist, inserted at scheduled show times
#   bootstrap         → fallback when music_library runs dry
#
# Stream: Icecast on localhost:8000/live.mp3

%include "schedule.liq"

# ---------------------------------------------------------------------------
# Paths (keep in sync with config.yaml)
# ---------------------------------------------------------------------------

# Set RADIO_ROOT in your environment (launchd plist or shell profile).
# Default: parent of the liquidsoap/ directory — works when run as:
#   liquidsoap liquidsoap/radio.liq   (from repo root)
library_root = getenv("RADIO_ROOT") ^ "/radio-library"
music_dir      = library_root ^ "/music"
dj_dir         = library_root ^ "/dj-segments"
shows_dir      = library_root ^ "/shows"
station_ids_dir = library_root ^ "/station-ids"
bootstrap_dir  = library_root ^ "/bootstrap"

# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

# Music library — watchfolder picks up new files as they arrive
music = playlist.reloadable(music_dir, mode="random", mime_type="audio/mpeg")

# Bootstrap — emergency fallback, always available
bootstrap = playlist(bootstrap_dir, mode="randomize", mime_type="audio/mpeg")

# Station IDs — short jingles, randomized
station_ids = playlist(station_ids_dir, mode="randomize", mime_type="audio/mpeg")

# Shows — talk show episodes, scheduled in schedule.liq
shows = playlist.reloadable(shows_dir, mode="queue", mime_type="audio/mpeg")

# ---------------------------------------------------------------------------
# DJ segment injection
# ---------------------------------------------------------------------------
# Each music track named TIMESTAMP_track_NNN.mp3 has a matching
# TIMESTAMP_intro_NNN.mp3 in the DJ segments folder.
# We try to find and play the matching intro before each music track.

def dj_intro_for(path) =
  basename = string.replace(pattern="(_track_)", by="_intro_", path)
  intro_path = dj_dir ^ "/" ^ file.basename(basename)
  if file.exists(intro_path) then
    [single(intro_path)]
  else
    []
  end
end

# Wrap music source to inject DJ intro before each track
music_with_dj = source.dynamic.handler(music, fun (path) ->
  dj_intro_for(path) @ [single(path)]
)

# ---------------------------------------------------------------------------
# Crossfade settings
# ---------------------------------------------------------------------------

crossfade_duration = 2.0

music_cf = crossfade(
  duration=crossfade_duration,
  music_with_dj
)

# ---------------------------------------------------------------------------
# Station ID insertion (every ~25 minutes)
# ---------------------------------------------------------------------------

station_id_interval = 25.0 * 60.0  # seconds

jingles = rotate(weights=[1, 20], [station_ids, music_cf])

# ---------------------------------------------------------------------------
# Show block routing — see schedule.liq for time windows
# ---------------------------------------------------------------------------
# Shows take priority over general rotation when scheduled.

final = switch(
  track_sensitive=true,
  [
    # Named recurring shows (highest priority)
    (meditation_monday_active,  shows),
    (jazz_at_dusk_active,       music_cf),   # jazz block uses music lib filtered by genre
    (sunday_folk_active,        music_cf),   # folk block uses music lib filtered

    # General time-of-day rotation
    (true, jingles)
  ]
)

# Ultimate fallback if music library runs dry
with_fallback = fallback(track_sensitive=false, [final, bootstrap])

# ---------------------------------------------------------------------------
# Audio normalization
# ---------------------------------------------------------------------------

normalized = normalize(with_fallback)

# ---------------------------------------------------------------------------
# Icecast output
# ---------------------------------------------------------------------------

output.icecast(
  %mp3(bitrate=128, samplerate=44100, stereo=true),
  host="localhost",
  port=8000,
  password=getenv("ICECAST_SOURCE_PASSWORD"),
  mount="/live.mp3",
  name="Blue Hour Radio",
  description="The hour between day and night.",
  genre="Jazz, Folk, Country",
  url="http://localhost:8000/live.mp3",
  public=true,
  normalized
)

# ---------------------------------------------------------------------------
# Telnet control (for watchdog health checks)
# ---------------------------------------------------------------------------
settings.server.telnet.set(true)
settings.server.telnet.port.set(1234)
settings.server.telnet.bind_addr.set("127.0.0.1")
