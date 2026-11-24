// Coalesces "read the server status now" requests on the WebSocket. Pure: the
// transport is injected, so tests/js/status-refresher.test.js can pin it.
//
// Snapserver sends its change notifications to every control connection except
// the one that made the change, so this page learns of its own writes only by
// asking again. At most one status request is in flight; any wants that arrive
// meanwhile collapse into one trailing request sent when the answer lands, so a
// volume drag (up to 10 writes a second) costs one request in flight plus one.

export function createStatusRefresher({ send }) {
  let inFlight = false;
  let trailing = false;

  function fire() {
    inFlight = true;
    trailing = false;
    send();
  }

  return {
    want() {
      if (inFlight) {
        trailing = true;
        return;
      }
      fire();
    },
    answered() {
      inFlight = false;
      if (trailing) fire();
    },
    // The socket closed or reopened: an in-flight request will never be answered.
    reset() {
      inFlight = false;
      trailing = false;
    },
  };
}
