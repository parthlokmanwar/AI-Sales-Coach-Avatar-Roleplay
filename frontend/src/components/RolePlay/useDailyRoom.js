/*
 * useDailyRoom — joins the Tavus conversation as a first-class Daily call
 * instead of embedding it in a cross-origin iframe.
 *
 * WHY THIS EXISTS: Tavus's `conversation_url` is literally a Daily room
 * (https://tavus.daily.co/<conversation_id>), and Tavus supports joining it
 * with the Daily SDK for a custom UI. Inside an iframe the avatar's video is
 * cross-origin, so the only way to record it was getDisplayMedia — which does
 * not exist on iOS Safari or Android Chrome. Joining the room ourselves turns
 * the avatar into ordinary same-origin MediaStreamTracks in this page, which
 * MediaRecorder CAN record on a phone. That is the whole point of this module.
 *
 * It exposes the remote (avatar) tracks so useSessionRecorder can record them,
 * plus the mic/camera controls the iframe used to provide.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import DailyIframe from "@daily-co/daily-js";

export default function useDailyRoom(roomUrl, { enabled = true } = {}) {
  const [joined, setJoined]       = useState(false);
  const [error, setError]         = useState("");
  const [avatarOn, setAvatarOn]   = useState(false);  // remote video is flowing
  const [micOn, setMicOn]         = useState(true);
  const [camOn, setCamOn]         = useState(true);
  // The rep's own mic track, shared with liveCoach so it doesn't open a second
  // capture of the same device (which iOS Safari handles badly).
  const [micTrack, setMicTrack]   = useState(null);

  const callRef       = useRef(null);
  const remoteVideoEl = useRef(null);   // <video> for the avatar
  const localVideoEl  = useRef(null);   // <video> for the rep's self-view
  const tracksRef     = useRef({ videoTrack: null, audioTracks: [] });

  /** Snapshot of what to record: the avatar's video track (fallback), both
   *  audio tracks, and the two <video> elements so the recorder can composite
   *  the avatar AND the rep's camera into a single frame. */
  const getTracks = useCallback(() => ({
    videoTrack:  tracksRef.current.videoTrack,
    audioTracks: tracksRef.current.audioTracks.filter(Boolean),
    remoteEl:    remoteVideoEl.current,
    localEl:     localVideoEl.current,
  }), []);

  useEffect(() => {
    if (!enabled || !roomUrl) return;
    let cancelled = false;

    // createCallObject (not a frame) — we render the media ourselves.
    const call = DailyIframe.createCallObject({
      subscribeToTracksAutomatically: true,
    });
    callRef.current = call;

    const syncTracks = () => {
      if (cancelled) return;
      const participants = call.participants();
      const local = participants.local;

      // The avatar is the one participant that isn't us.
      const remote = Object.values(participants).find((p) => p && !p.local);
      const rv = remote && remote.tracks && remote.tracks.video;
      const ra = remote && remote.tracks && remote.tracks.audio;
      const la = local && local.tracks && local.tracks.audio;

      const videoTrack = rv && rv.state === "playable" ? rv.persistentTrack : null;
      const audioTracks = [
        ra && ra.state === "playable" ? ra.persistentTrack : null,
        la && la.state === "playable" ? la.persistentTrack : null,
      ].filter(Boolean);
      tracksRef.current = { videoTrack, audioTracks };
      setAvatarOn(!!videoTrack);
      const localMic = la && la.state === "playable" ? la.persistentTrack : null;
      setMicTrack((prev) => (prev === localMic ? prev : localMic));

      // Attach media to the elements. Audio for the avatar must play through an
      // element or the rep hears nothing — we are no longer inside Tavus's UI.
      if (remoteVideoEl.current) {
        const want = [videoTrack, ra && ra.state === "playable" ? ra.persistentTrack : null].filter(Boolean);
        if (want.length) {
          const cur = remoteVideoEl.current.srcObject;
          const same = cur && cur.getTracks().length === want.length &&
                       want.every((t) => cur.getTracks().includes(t));
          if (!same) remoteVideoEl.current.srcObject = new MediaStream(want);
        }
      }
      if (localVideoEl.current) {
        const lv = local && local.tracks && local.tracks.video;
        const lvt = lv && lv.state === "playable" ? lv.persistentTrack : null;
        const cur = localVideoEl.current.srcObject;
        if (lvt && (!cur || !cur.getTracks().includes(lvt))) {
          localVideoEl.current.srcObject = new MediaStream([lvt]);
        }
      }
    };

    const onJoined = () => { if (!cancelled) { setJoined(true); syncTracks(); } };
    const onError = (e) => {
      if (cancelled) return;
      setError(e?.errorMsg || "Couldn't connect to the call.");
    };

    call.on("joined-meeting", onJoined);
    call.on("participant-joined", syncTracks);
    call.on("participant-updated", syncTracks);
    call.on("participant-left", syncTracks);
    call.on("track-started", syncTracks);
    call.on("track-stopped", syncTracks);
    call.on("error", onError);

    call.join({ url: roomUrl, startVideoOff: false, startAudioOff: false })
      .catch(onError);

    return () => {
      cancelled = true;
      try {
        call.off("joined-meeting", onJoined);
        call.off("participant-joined", syncTracks);
        call.off("participant-updated", syncTracks);
        call.off("participant-left", syncTracks);
        call.off("track-started", syncTracks);
        call.off("track-stopped", syncTracks);
        call.off("error", onError);
      } catch { /* noop */ }
      // leave() then destroy() — destroy alone can leave the room occupied.
      Promise.resolve(call.leave()).catch(() => {}).finally(() => {
        try { call.destroy(); } catch { /* noop */ }
      });
      callRef.current = null;
      tracksRef.current = { videoTrack: null, audioTracks: [] };
    };
  }, [roomUrl, enabled]);

  const toggleMic = useCallback(() => {
    const call = callRef.current;
    if (!call) return;
    const next = !call.localAudio();
    call.setLocalAudio(next);
    setMicOn(next);
  }, []);

  const toggleCam = useCallback(() => {
    const call = callRef.current;
    if (!call) return;
    const next = !call.localVideo();
    call.setLocalVideo(next);
    setCamOn(next);
  }, []);

  return {
    joined, error, avatarOn, micOn, camOn, micTrack,
    remoteVideoEl, localVideoEl, getTracks, toggleMic, toggleCam,
  };
}
