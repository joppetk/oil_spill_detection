// server/rtc.js
import wrtc from 'wrtc';
const { RTCPeerConnection } = wrtc;

export async function createPeerForClient(ws, gps, scheduler, sdpOffer) {
  // For LAN testing, skip STUN to simplify
  const pc = new RTCPeerConnection({ iceServers: [] });

  let unsub = null;

  /* pc.ondatachannel = (ev) => {
    const ch = ev.channel;
    console.log('[RTC] datachannel from client:', ch.label);
    if (ch.label !== 'hazard') return;

    ch.onopen = () => {
      console.log('[RTC] hazard channel OPEN (server)');
      unsub = scheduler.subscribe(gps, (frame) => {
        if (ch.readyState === 'open') ch.send(JSON.stringify(frame));
      });
      // echo heartbeat back so you see rx on server
      ch.onmessage = (e) => { if (typeof e.data === 'string' && e.data.includes('ping')) console.log('[RTC] ping'); };
      // send a hello immediately
      //ch.send(JSON.stringify({ header:{ kind:'hello', ts: Date.now() }, topo:{}, bbox:null }));
    };
    ch.onclose = () => { console.log('[RTC] hazard channel CLOSED'); unsub?.(); };
  };
 */

  pc.ondatachannel = (ev) => {
    const ch = ev.channel;
    if (ch.label !== 'hazard') return;

    let unsub = null;
    ch.onopen = () => {
      unsub = scheduler.subscribe(gps, (frame) => {
        if (ch.readyState !== 'open') return;
        const payload = JSON.stringify(frame);
        ch.send(payload);
        // metrics
        try {
          const bytes = Buffer.byteLength(payload, 'utf8');
          metrics.hazard.bytesSent += bytes;
          metrics.hazard.messagesSent += 1;
          metrics.hazard.lastSeq = frame?.header?.seq ?? null;
          metrics.hazard.lastSentAt = Date.now();
        } catch {}
      });
    };
    ch.onclose = () => unsub?.();
  };
  
  pc.onconnectionstatechange = () => console.log('[RTC] connectionState =', pc.connectionState);
  pc.oniceconnectionstatechange = () => console.log('[RTC] iceState =', pc.iceConnectionState);
  pc.onicecandidate = ({ candidate }) => candidate && ws.send(JSON.stringify({ type:'candidate', candidate }));

  await pc.setRemoteDescription({ type:'offer', sdp: sdpOffer });
  const answer = await pc.createAnswer();
  await pc.setLocalDescription(answer);
  ws.send(JSON.stringify({ type:'answer', sdp: pc.localDescription.sdp }));

  return { pc, close: () => { try { pc.close(); } catch {} } };
}