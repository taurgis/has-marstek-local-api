# Wi-Fi UDP reliability research

Research scope: local UDP JSON-RPC to a Marstek Venus E 3.0 over a Quectel
FC41D/FC41DA Wi-Fi path. This note uses vendor manuals, standards/RFCs,
Python documentation, and first-party project source. The firmware strings
and the 4/6 versus 6/6 live-probe results are supplied observations, not
claims derived from the sources below.

## Conclusions

- UDP provides neither delivery nor duplicate protection, and RFC 1122
  explicitly assigns retransmission and other end-to-end reliability work to
  the application when required
  ([RFC 768](https://www.rfc-editor.org/rfc/rfc768.html),
  [RFC 1122 §4.1.1](https://www.rfc-editor.org/rfc/rfc1122.html#section-4.1.1)).
- Wi-Fi power save can delay a downlink while an AP buffers it for a sleeping
  station, but the public Quectel manual does not establish that the Marstek
  enables such a mode or document a chip-specific “first packet after idle is
  lost” behavior
  ([RFC 8352 §3.1](https://www.rfc-editor.org/rfc/rfc8352.html#section-3.1),
  [Quectel FC41D AT manual](https://developer.quectel.com/doc/shortrange/FC41D/en/Resources/AT-Firmware/AT-Commands-Manual.html)).
- A 100 ms resend happens to be near the commonly used 100-TU beacon interval,
  but that does not make 100 ms a portable retry recommendation. A 300 ms DTIM
  is not a standard default, and DTIM principally schedules buffered
  broadcast/multicast rather than ordinary unicast
  ([Linux Wireless power-save documentation](https://wireless.docs.kernel.org/en/latest/en/developers/documentation/ieee80211/power-savings.html),
  [IEEE 802.11-2020 standard record](https://standards.ieee.org/ieee/802.11/7028/)).
- The live 6/6 result justifies a larger controlled experiment. Six trials are
  not enough to identify the mechanism or choose a production delay; interval
  precision and required sample size depend on the chosen confidence and
  error bounds
  ([NIST confidence intervals](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm),
  [NIST sample-size guidance](https://itl.nist.gov/div898/handbook/ppc/section3/ppc333.htm)).

## 1. Quectel FC41D/FC41DA path

Quectel's current public manual covers the FC41D family and lists Wi-Fi,
BLE, TCP/UDP, SSL, MQTT, HTTP(S), and GPIO AT commands. Quectel also publishes
AT firmware named `FC41DA...`, so “FC41DA” appears to be a firmware/build
designation rather than a separately documented command set
([FC41D AT manual](https://developer.quectel.com/doc/shortrange/FC41D/en/Resources/AT-Firmware/AT-Commands-Manual.html),
[FC41D AT firmware page](https://developer.quectel.com/doc/shortrange/FC41D/en/Resources/AT-Firmware/AT-Firmware.html)).

For `AT+QIOPEN`, `"UDP SERVICE"` opens a local UDP service, requires a local
port, and supplies the remote address and port per send. Access mode 0 is
buffered AT-command access and mode 1 is direct-push URC access; transparent
mode 2 is unavailable for `"UDP SERVICE"`
([Quectel `AT+QIOPEN`](https://developer.quectel.com/doc/shortrange/FC41D/en/Resources/AT-Firmware/AT-Commands-Manual.html#at-qiopen)).

For that service, `AT+QISEND` accepts a destination IP and port, permits
0–1460 payload bytes, and reports the number of bytes written to the socket.
Its documented maximum response time is 300 ms. This is an AT-command response
bound; the manual does not call it a Wi-Fi retry, beacon, DTIM, or end-to-end
reply timeout
([Quectel `AT+QISEND`](https://developer.quectel.com/doc/shortrange/FC41D/en/Resources/AT-Firmware/AT-Commands-Manual.html#at-qisend)).

In buffered mode, `+QIURC: "recv",<socketID>` announces receive data and no
new receive URC is proactively reported until the buffered data is drained
with `AT+QIRD`. In direct-push mode, a UDP service's receive URC also carries
length, source address, and source port
([Quectel `+QIURC: "recv"`](https://developer.quectel.com/doc/shortrange/FC41D/en/Resources/AT-Firmware/AT-Commands-Manual.html#qiurc-recv)).
This makes prompt draining relevant inside device firmware, but does not show
that packets are being lost between Home Assistant and the AP.

The public power commands do not describe connected Wi-Fi modem sleep:
`AT+QLOWPOWER` disconnects Wi-Fi/BLE, while wake from `AT+QDEEPSLEEP` reboots
the module. The manual exposes no documented DTIM, listen-interval, or
connected-station power-save control, so the Marstek's associated-idle policy
cannot be inferred from this AT interface
([Quectel `AT+QLOWPOWER` and `AT+QDEEPSLEEP`](https://developer.quectel.com/doc/shortrange/FC41D/en/Resources/AT-Firmware/AT-Commands-Manual.html#at-qlowpower)).

No primary Quectel source located in this research documents deterministic
first-datagram loss after idle for FC41D/FC41DA.

## 2. What 802.11 power save does and does not establish

In 802.11 power-save mode, an AP buffers frames for a sleeping station. The
station wakes at its listen interval, which may be a multiple of the beacon
interval, reads the beacon indication, and can request buffered data
([RFC 8352 §3.1](https://www.rfc-editor.org/rfc/rfc8352.html#section-3.1)).
Linux's first-party wireless documentation describes the corresponding TIM
bitmap and PS-Poll retrieval for buffered unicast
([Linux Wireless power-save documentation](https://wireless.docs.kernel.org/en/latest/en/developers/documentation/ieee80211/power-savings.html)).

The beacon interval is expressed in 1024-microsecond time units and is
*typically* 100 TU, about 102.4 ms. “Typically” is not a protocol guarantee
([Linux Wireless power-save documentation](https://wireless.docs.kernel.org/en/latest/en/developers/documentation/ieee80211/power-savings.html)).

DTIM is a special TIM used to release buffered broadcast and multicast. Its
one-byte period is a count of beacon intervals. Thus “about 300 ms” follows
only from a particular configuration such as DTIM period 3 with a 100-TU
beacon; it is neither a mandated default nor the right timer to assume for a
unicast request
([Linux Wireless power-save documentation](https://wireless.docs.kernel.org/en/latest/en/developers/documentation/ieee80211/power-savings.html)).

These mechanisms support a hypothesis of variable first-response latency.
They do not prove that the FC41D is asleep, that a first frame is dropped
rather than delayed, or that a second request at one beacon interval wakes it.
Those require packet capture/AP telemetry and a larger randomized probe.

## 3. UDP reliability belongs above UDP

RFC 768 states that UDP delivery and duplicate protection are not guaranteed
([RFC 768](https://www.rfc-editor.org/rfc/rfc768.html)). RFC 1122 says a UDP
application must handle end-to-end problems such as retransmission for
reliable delivery, packetization, flow control, and congestion avoidance when
needed
([RFC 1122 §4.1.1](https://www.rfc-editor.org/rfc/rfc1122.html#section-4.1.1)).
Application retries are therefore justified; those RFCs do not prescribe a
retry count or timer for a LAN appliance.

## 4. First-party `jaapp/ha-marstek-local-api` behavior

At pinned commit
[`624881c82941bdd68bf229299ac3b9606971caca`](https://github.com/jaapp/ha-marstek-local-api/tree/624881c82941bdd68bf229299ac3b9606971caca),
the constants are:

| Setting | Value |
|---|---:|
| `COMMAND_TIMEOUT` | 15 s |
| `COMMAND_MAX_ATTEMPTS` | 3 |
| `COMMAND_BACKOFF_BASE` | 1.5 s |
| `COMMAND_BACKOFF_FACTOR` | 2.0 |
| `COMMAND_BACKOFF_MAX` | 12.0 s |
| `COMMAND_BACKOFF_JITTER` | 0.4 s |

Source:
[`const.py` lines 21–29](https://github.com/jaapp/ha-marstek-local-api/blob/624881c82941bdd68bf229299ac3b9606971caca/custom_components/marstek_local_api/const.py#L21-L29).
That file also defines `MAX_RETRIES = 3` and `RETRY_DELAY = 2`, but `api.py`
imports and uses the `COMMAND_*` family, not those aliases
([`api.py` lines 13–23](https://github.com/jaapp/ha-marstek-local-api/blob/624881c82941bdd68bf229299ac3b9606971caca/custom_components/marstek_local_api/api.py#L13-L23)).

`send_command()` registers one response handler, reuses one JSON-RPC ID and
payload across attempts, and matches both the ID and expected source host
([`api.py` lines 228–277](https://github.com/jaapp/ha-marstek-local-api/blob/624881c82941bdd68bf229299ac3b9606971caca/custom_components/marstek_local_api/api.py#L228-L277)).
Each attempt sends once and waits up to the effective timeout with
`asyncio.wait_for(response_event.wait(), ...)`
([`api.py` lines 279–300](https://github.com/jaapp/ha-marstek-local-api/blob/624881c82941bdd68bf229299ac3b9606971caca/custom_components/marstek_local_api/api.py#L279-L300)).
After timeout or a non-API exception, attempts 1 and 2 sleep for
`min(1.5 * 2 ** (attempt - 1), 12) + uniform(0, 0.4)`, giving 1.5–1.9 s
and 3.0–3.4 s retry delays; API error responses are raised without retry
([`api.py` lines 340–390](https://github.com/jaapp/ha-marstek-local-api/blob/624881c82941bdd68bf229299ac3b9606971caca/custom_components/marstek_local_api/api.py#L340-L390),
[`api.py` lines 420–426](https://github.com/jaapp/ha-marstek-local-api/blob/624881c82941bdd68bf229299ac3b9606971caca/custom_components/marstek_local_api/api.py#L420-L426)).
This is evidence of another client's policy, not evidence that these values
are optimal for FC41D power save.

## 5. `asyncio.wait_for` versus `asyncio.wait`

On timeout, `asyncio.wait_for(aw, timeout)` cancels `aw`, waits for
cancellation to complete, and raises `TimeoutError`; `asyncio.shield()` is the
documented way to protect an awaitable from that cancellation
([Python `wait_for`](https://docs.python.org/3/library/asyncio-task.html#asyncio.wait_for),
[Python `shield`](https://docs.python.org/3/library/asyncio-task.html#asyncio.shield)).
In contrast, `asyncio.wait(tasks, timeout=...)` returns pending tasks and does
not cancel them
([Python `wait`](https://docs.python.org/3/library/asyncio-task.html#asyncio.wait)).

The consequence depends on the receive primitive:

- If each attempt awaits a newly created `Event.wait()` coroutine, timing it
  out does not destroy the reusable `Event`; a later datagram can still set it.
- If the shared response object is a `Future` or `Task`, passing it directly to
  `wait_for` cancels that shared object. To permit a late response or another
  attempt to complete the same future, use `wait`, or use
  `wait_for(shield(future), timeout)` and keep a strong reference to the task
  ([Python `wait_for`](https://docs.python.org/3/library/asyncio-task.html#asyncio.wait_for),
  [Python `shield`](https://docs.python.org/3/library/asyncio-task.html#asyncio.shield)).

## 6. Immediate, 100 ms, and 300 ms duplicates

No primary source found here recommends any of these intervals for unicast UDP
to an idle FC41D or generic IoT station:

- **0 ms:** UDP permits duplicates but does not say immediate duplication
  improves delivery. Two back-to-back packets provide little timing diversity
  and double request processing
  ([RFC 768](https://www.rfc-editor.org/rfc/rfc768.html)).
- **100 ms:** this is close to the typical 102.4 ms beacon interval, but the
  actual beacon and station listen intervals are configurable
  ([Linux Wireless power-save documentation](https://wireless.docs.kernel.org/en/latest/en/developers/documentation/ieee80211/power-savings.html),
  [RFC 8352 §3.1](https://www.rfc-editor.org/rfc/rfc8352.html#section-3.1)).
- **300 ms:** Quectel documents 300 ms as the maximum response time for
  `AT+QISEND`, not as a network retry timer. A roughly 300 ms DTIM is only one
  possible AP configuration and concerns buffered group traffic
  ([Quectel `AT+QISEND`](https://developer.quectel.com/doc/shortrange/FC41D/en/Resources/AT-Firmware/AT-Commands-Manual.html#at-qisend),
  [Linux Wireless power-save documentation](https://wireless.docs.kernel.org/en/latest/en/developers/documentation/ieee80211/power-savings.html)).

The observed 100 ms improvement is therefore useful device-specific evidence,
not a documented protocol constant. Compare several delays after controlled
idle periods, randomize their order, record late/duplicate replies, and use
enough trials to estimate a confidence interval before selecting a default
([NIST confidence intervals](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm),
[NIST sample-size guidance](https://itl.nist.gov/div898/handbook/ppc/section3/ppc333.htm)).

## 7. Receive API and `SO_RCVBUF`

Both Python receive designs are supported:

- `DatagramProtocol` instances are constructed through
  `loop.create_datagram_endpoint()`, and `datagram_received(data, addr)` is
  called for received datagrams
  ([Python transport/protocol docs](https://docs.python.org/3/library/asyncio-protocol.html#datagram-protocols)).
- `loop.sock_recvfrom()` is an asynchronous `recvfrom()` for a socket that the
  caller must place in non-blocking mode
  ([Python event-loop docs](https://docs.python.org/3/library/asyncio-eventloop.html#asyncio.loop.sock_recvfrom)).

Python does not designate one of these UDP APIs as inherently more reliable.
Changing APIs cannot recover a datagram lost before it reaches the host
socket. On BSD systems including macOS, Python warns that datagram send flow
control cannot reliably detect overload: excess packets can be dropped and
`ENOBUFS` may or may not be reported
([Python datagram protocol docs](https://docs.python.org/3/library/asyncio-protocol.html#datagram-protocols)).

`SO_RCVBUF` sets the kernel socket receive-buffer capacity; Linux additionally
documents its doubling/accounting and system limits
([Linux `socket(7)`](https://man7.org/linux/man-pages/man7/socket.7.html),
[Python `setsockopt`](https://docs.python.org/3/library/socket.html#socket.socket.setsockopt)).
It can help only if bursts are overflowing the Home Assistant host's receive
queue. It does not address AP buffering, sleeping-station behavior, or loss in
the FC41D/Marstek receive path. An asyncio transport can expose its underlying
socket through `transport.get_extra_info("socket")`
([Python transport docs](https://docs.python.org/3/library/asyncio-protocol.html#asyncio.BaseTransport.get_extra_info)).

## 8. Ethernet boundary

WCH documents CH395 as an Ethernet controller with an integrated TCP/IP stack
and UDP support
([WCH CH395 datasheet](https://www.wch-ic.com/downloads/CH395DS1_PDF.html)).
Ethernet is not subject to 802.11 beacon/DTIM power save, so an Ethernet-only
firmware fix cannot establish the cause or cure of Wi-Fi loss. The supplied
firmware-150 string finding remains local reverse-engineering evidence rather
than a public vendor statement.

## Plugin-side changes: justified versus speculative

### Justified

1. Add bounded application-layer retries for requests whose reply is required;
   UDP itself supplies no delivery guarantee
   ([RFC 768](https://www.rfc-editor.org/rfc/rfc768.html),
   [RFC 1122 §4.1.1](https://www.rfc-editor.org/rfc/rfc1122.html#section-4.1.1)).
2. Correlate replies by JSON-RPC ID and source address, accept the first valid
   reply, and harmlessly ignore late duplicates; UDP has no duplicate
   protection
   ([JSON-RPC 2.0 response IDs](https://www.jsonrpc.org/specification#response_object),
   [RFC 768](https://www.rfc-editor.org/rfc/rfc768.html)).
3. Preserve the pending response future across a short retry window when late
   replies are useful, using `asyncio.wait` or `wait_for(shield(...))` rather
   than cancelling that future
   ([Python waiting primitives](https://docs.python.org/3/library/asyncio-task.html#waiting-primitives)).
4. Keep retries bounded and avoid concurrent request bursts; RFC 1122 includes
   congestion avoidance among the responsibilities a UDP application may need
   to provide
   ([RFC 1122 §4.1.1](https://www.rfc-editor.org/rfc/rfc1122.html#section-4.1.1)).
5. Instrument first-attempt success, retry success, response latency, duplicate
   replies, idle duration, and interface type before tuning a default. The
   resulting success proportions need stated confidence bounds
   ([NIST confidence intervals](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm)).

### Speculative pending stronger measurements

1. Sending an unconditional immediate duplicate.
2. Treating 100 ms as a portable “wake copy” interval.
3. Treating 300 ms as a DTIM-derived or Quectel network retry interval.
4. Enabling or tuning FC41D connected Wi-Fi power save through undocumented AT
   commands.
5. Increasing `SO_RCVBUF` without evidence of host receive-queue overflow.
6. Replacing `sock_recvfrom` with `DatagramProtocol` solely to cure RF-side
   first-packet loss.
7. Copying jaapp's 15 s × 3 policy as though it were a vendor recommendation;
   it is only that project's implementation choice
   ([pinned `const.py`](https://github.com/jaapp/ha-marstek-local-api/blob/624881c82941bdd68bf229299ac3b9606971caca/custom_components/marstek_local_api/const.py#L21-L29)).

## Plugin policy (this repository)

Bounded application retries stay justified by RFC 768 / RFC 1122. The
implementation opts in only after `FirmwareProfile.openapi_wifi_retransmit_safe`
(known family, known Control generation, not reset-prone). Read-only methods
may send a silent-wait copy at 500 ms, then wait the remaining configured
timeout (cap: two datagrams). Writes and unmarked IPs stay one-shot so extra
UDP cannot hit `ES.SetMode` or reset-prone Control. `asyncio.wait` keeps the
pending JSON-RPC future alive across those waits. Ethernet and dual-homed
Ethernet IPs typically reply before 500 ms and never get the copy.
