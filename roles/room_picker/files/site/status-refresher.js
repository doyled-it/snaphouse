// Coalesces "read the server status now" requests on the WebSocket. Pure: the
// transport is injected, so tests/js/status-refresher.test.js can pin it.
//
// Snapserver sends its change notifications to every control connection except
// the one that made the change, so this page learns of its own writes only by
// asking again. At most one status request is in flight; any wants that arrive
// meanwhile collapse into one trailing request sent when the answer lands, so a
// volume drag (up to 10 writes a second) costs one request in flight plus one.
//
// A request in flight longer than `deadlineMs` is treated as lost (an unanswered
// or malformed reply on a socket that stays open), so the next want() sends a new
// one instead of waiting forever. `send` returns the request's id so an error
// reply to it can be recognised with owns().

export function createStatusRefresher({ send, now, deadlineMs = 5000 }) {
  let inFlight = false;
  let sentAt = 0;
  let sentId = null;
  let trailing = false;

  function fire() {
    inFlight = true;
    trailing = false;
    sentAt = now();
    sentId = send();
  }

  return {
    want() {
      if (inFlight && now() - sentAt <= deadlineMs) {
        trailing = true;
        return;
      }
      fire();
    },
    // True for the id of the status request now in flight.
    owns(id) {
      return inFlight && id !== undefined && id === sentId;
    },
    answered() {
      inFlight = false;
      sentId = null;
      if (trailing) fire();
    },
    // The socket closed or reopened: an in-flight request will never be answered.
    reset() {
      inFlight = false;
      sentId = null;
      trailing = false;
    },
  };
}
