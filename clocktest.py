"""Four-timestamp clock probe and a mIRC client using Windows' wall clock."""
from dataclasses import dataclass
from datetime import datetime, timezone
import base64

CLOCK_TEST_TTL = 300
CLOCK_READER_PROGRAM = ('for (;;) { [void][Console]::ReadLine(); '
                        '[Console]::WriteLine([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()); }')


@dataclass
class ClockTest:
    owner: str
    nick: str
    expires: float
    phase: str = 'issued'
    client_send_us: int | None = None
    server_receive_us: int | None = None
    server_send_us: int | None = None


@dataclass(frozen=True)
class ClockProbe:
    token: str


def epoch_us(stamp):
    elapsed = stamp.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (elapsed.days * 86400 + elapsed.seconds) * 1_000_000 + elapsed.microseconds


def mirc_script(token, channel, bot_nick, *, private_replies=False):
    """Warm PowerShell before measuring; retain it for the automatic reply.

    Millisecond timestamps are sampled through local pipes. These add some
    client overhead, so RTT/one-way/offset remain estimates, not NTP precision.
    -EncodedCommand keeps PowerShell punctuation out of mIRC evaluation; the
    complete readable program is included in the generated script's comments.
    """
    name = f'lt{token}'
    program = CLOCK_READER_PROGRAM
    encoded = base64.b64encode(program.encode('utf-16-le')).decode('ascii')
    destination = bot_nick if private_replies else channel
    listener = '?' if private_replies else channel
    return f'''; LeetBot clock test {token}, issued to one nickname for five minutes.
; No downloads or clock changes. Uses Windows PowerShell and mIRC COM.
; PowerShell waits for a pipe request, then prints UTC Unix milliseconds:
; {program}
; Start in {channel} on the same IRC network. COM must be enabled in mIRC.
alias leetclock_{token} {{
  if ($chan != {channel}) {{ echo -a Run /leetclock_{token} in {channel}. | return }}
  if ($com({name}s)) {{ echo -a This test is already running. | return }}
  comopen {name}s WScript.Shell
  if ($comerr) {{ echo -a Cannot open Windows COM. Check mIRC COM permissions. | return }}
  noop $com({name}s,Exec,1,bstr,powershell -NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand {encoded},dispatch* {name}p)
  if ($comerr) {{ leetclock_cleanup_{token} | echo -a PowerShell could not start. | return }}
  noop $com({name}p,StdIn,2,dispatch* {name}i)
  if ($comerr) {{ leetclock_cleanup_{token} | return }}
  noop $com({name}p,StdOut,2,dispatch* {name}o)
  if ($comerr) {{ leetclock_cleanup_{token} | return }}
  ; Warm the process before taking the send timestamp.
  noop $com({name}i,WriteLine,1,bstr,warmup)
  noop $com({name}o,ReadLine,1)
  if ($comerr) {{ leetclock_cleanup_{token} | return }}
  .timer{name} 1 120 leetclock_timeout_{token}
  noop $com({name}i,WriteLine,1,bstr,sample)
  noop $com({name}o,ReadLine,1)
  var %stamp = $com({name}o).result
  if (%stamp !isnum) {{ leetclock_cleanup_{token} | echo -a No millisecond clock sample received. | return }}
  msg {destination} !timetest {token} start %stamp
}}
on *:TEXT:!leetclock {token} *:{listener}:{{
  if ($nick != {bot_nick}) return
  if (!$com({name}o)) return
  noop $com({name}i,WriteLine,1,bstr,sample)
  noop $com({name}o,ReadLine,1)
  var %stamp = $com({name}o).result
  if (%stamp isnum) msg {destination} !timetest {token} result %stamp
  leetclock_cleanup_{token}
}}
alias leetclock_timeout_{token} {{
  leetclock_cleanup_{token}
  echo -a LeetBot clock test timed out. Request !timetest again.
}}
alias leetclock_cleanup_{token} {{
  .timer{name} off
  if ($com({name}p)) noop $com({name}p,Terminate,1)
  if ($com({name}o)) comclose {name}o
  if ($com({name}i)) comclose {name}i
  if ($com({name}p)) comclose {name}p
  if ($com({name}s)) comclose {name}s
}}
'''


def reusable_mirc_script(channel, bot_nick, *, private_replies=False):
    """Shared install: negotiate a new nickname-bound ID on every /leetclock.

    Correlate the offer with the current connection, nickname and request nonce.
    Only the configured bot can trigger sampling; never evaluate received text.
    """
    encoded = base64.b64encode(CLOCK_READER_PROGRAM.encode('utf-16-le')).decode('ascii')
    destination = bot_nick if private_replies else channel
    listener = '?' if private_replies else channel
    result_location = f'a private chat with {bot_nick}' if private_replies else channel
    return f'''; LeetBot reusable clock test v1. Install once in a separate Remote file.
; Run /leetclock in {channel}; /leetclock cancel releases the clock reader.
; Shared by all players: each run negotiates a fresh nickname-bound test ID.
; Windows PowerShell reads milliseconds; no downloads or clock changes.
; Readable PowerShell program (encoded below to protect mIRC punctuation):
; {CLOCK_READER_PROGRAM}
alias leetclock {{
  if ($1 == cancel) {{ leetclock_cleanup | echo -a LeetBot clock test cancelled. | return }}
  if ($status != connected) {{ echo -a Connect to IRC first. | return }}
  if ($chan != {channel}) {{ echo -a Run /leetclock in {channel}. | return }}
  if (!$nick($chan,{bot_nick})) {{ echo -a {bot_nick} is not in this channel. | return }}
  if (%leetclock.phase) {{ echo -a A test is running. Wait or use /leetclock cancel. | return }}
  leetclock_cleanup
  set %leetclock.cid $cid
  set %leetclock.request $+($rand(100000,999999),$rand(100000,999999))
  set %leetclock.phase request
  .timerleetclock 1 120 leetclock_timeout
  msg {destination} !timetest request %leetclock.request
  echo -a LeetBot: requesting a fresh clock test. Results will appear in {result_location}.
}}
on *:TEXT:!leetclock *:{listener}:{{
  if ($nick != {bot_nick}) return
  if ($cid != %leetclock.cid) return
  if ((%leetclock.phase == request) && ($2 == %leetclock.request)) {{
    if ($3 == error) {{ leetclock_cleanup | echo -a LeetBot: $4- | return }}
    if (($3 != issued) || ($4 != $me) || (!$regex($5,/^[a-f0-9]{{12}}$/))) return
    set %leetclock.token $5
    set %leetclock.phase starting
    if (!$leetclock_open) {{ leetclock_cleanup | echo -a Cannot read the Windows clock. Check COM permissions and Windows PowerShell. | return }}
    var %stamp = $leetclock_sample
    if (%stamp !isnum) {{ leetclock_cleanup | echo -a No millisecond clock sample received. | return }}
    set %leetclock.phase ready
    msg {destination} !timetest %leetclock.token start %stamp
    return
  }}
  if ((%leetclock.phase == ready) && ($2 == %leetclock.token) && ($3 == ready)) {{
    var %stamp = $leetclock_sample
    if (%stamp !isnum) {{ leetclock_cleanup | echo -a No millisecond clock sample received. | return }}
    msg {destination} !timetest %leetclock.token result %stamp
    leetclock_cleanup
  }}
}}
alias -l leetclock_open {{
  comopen leetclocks WScript.Shell
  if ($comerr) return $false
  noop $com(leetclocks,Exec,1,bstr,powershell -NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand {encoded},dispatch* leetclockp)
  if ($comerr) return $false
  noop $com(leetclockp,StdIn,2,dispatch* leetclocki)
  if ($comerr) return $false
  noop $com(leetclockp,StdOut,2,dispatch* leetclocko)
  if ($comerr) return $false
  ; Warm PowerShell before sampling T1. Startup time is outside the measurement.
  var %warm = $leetclock_sample
  if (%warm isnum) return $true
  return $false
}}
alias -l leetclock_sample {{
  noop $com(leetclocki,WriteLine,1,bstr,sample)
  if ($comerr) return
  noop $com(leetclocko,ReadLine,1)
  if ($comerr) return
  return $com(leetclocko).result
}}
alias leetclock_timeout {{
  leetclock_cleanup
  echo -a LeetBot clock test timed out. Wait five seconds, then run /leetclock again.
}}
alias leetclock_cleanup {{
  .timerleetclock off
  if ($com(leetclockp)) noop $com(leetclockp,Terminate,1)
  if ($com(leetclocko)) comclose leetclocko
  if ($com(leetclocki)) comclose leetclocki
  if ($com(leetclockp)) comclose leetclockp
  if ($com(leetclocks)) comclose leetclocks
  unset %leetclock.*
}}
on *:START:leetclock_cleanup
on *:DISCONNECT:{{
  if ($cid == %leetclock.cid) leetclock_cleanup
}}
on *:UNLOAD:leetclock_cleanup
'''
