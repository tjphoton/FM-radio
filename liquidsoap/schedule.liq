# Blue Hour Radio — Show Schedule
# Defines time predicates used by radio.liq switch() for show block routing.
#
# Liquidsoap time syntax:
#   {Dw HH:MM:SS - Dw HH:MM:SS}
#   D = day of week: 1=Monday, 2=Tuesday, ..., 7=Sunday
#   (no D prefix = every day)
#
# Schedule:
#   Monday 7:00-7:20pm    Meditation Mondays
#   Tue-Fri 5:00-5:45pm   Jazz at Dusk
#   Sunday 8:00-9:00am    Sunday Morning Folk

# Meditation Mondays: Monday 19:00-19:20
def meditation_monday_active() =
  { 1w19h00m-1w19h20m }
end

# Jazz at Dusk: Tuesday through Friday 17:00-17:45
def jazz_at_dusk_active() =
  { 2w17h00m-2w17h45m } or
  { 3w17h00m-3w17h45m } or
  { 4w17h00m-4w17h45m } or
  { 5w17h00m-5w17h45m }
end

# Sunday Morning Folk: Sunday 8:00-9:00
def sunday_folk_active() =
  { 7w08h00m-7w09h00m }
end

# Time-of-day helpers (used for generation context, not Liquidsoap routing)
# These are for reference — Liquidsoap routing uses the named show predicates above.
# General rotation fallback applies outside all named show windows.
#
# morning:   06:00-12:00  upbeat folk/country
# afternoon: 12:00-17:00  mellow jazz/instrumental
# evening:   17:00-22:00  jazz + meditation
# night:     22:00-06:00  ambient/slow, sleep-friendly
