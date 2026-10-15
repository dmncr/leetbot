"""Four-timestamp clock probe and a mIRC client using Windows' wall clock."""
from dataclasses import dataclass
from datetime import datetime, timezone
import base64

CLOCK_TEST_TTL = 300


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


def mirc_script(token, channel, bot_nick):
    """Warm PowerShell before measuring; retain it for the automatic reply.

    Millisecond timestamps are sampled through local pipes. These add some
    client overhead, so RTT/one-way/offset remain estimates, not NTP precision.
    -EncodedCommand keeps PowerShell punctuation out of mIRC evaluation; the
    complete readable program is included in the generated script's comments.
    """
    name = f'lt{token}'
    program = ('for (;;) { [void][Console]::ReadLine(); '
               '[Console]::WriteLine([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()); }')
    encoded = base64.b64encode(program.encode('utf-16-le')).decode('ascii')
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
  msg {channel} !timetest {token} start %stamp
}}
on *:TEXT:!leetclock {token} *:{channel}:{{
  if ($nick != {bot_nick}) return
  if (!$com({name}o)) return
  noop $com({name}i,WriteLine,1,bstr,sample)
  noop $com({name}o,ReadLine,1)
  var %stamp = $com({name}o).result
  if (%stamp isnum) msg {channel} !timetest {token} result %stamp
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
