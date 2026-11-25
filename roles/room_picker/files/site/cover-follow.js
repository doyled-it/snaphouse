// Which hues follow the now-playing cover. The page background tint follows the cover
// and resets to the defaults when the track clears. The screensaver keeps the hues of
// the last cover it had, and stays on its defaults until the first cover arrives.
// A token drops a result that lands after a newer cover or a clear.

export function createCoverFollower({ defaults, load, setPage, setSaver }) {
  let token = 0;
  return function follow(url) {
    token += 1;
    const mine = token;
    if (!url) {
      setPage(defaults);
      return;
    }
    load(url).then((hues) => {
      if (mine !== token) return;
      setPage(hues);
      setSaver(hues);
    });
  };
}
