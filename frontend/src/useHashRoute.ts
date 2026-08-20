import { useEffect, useState } from 'react';

function readHash(): string {
  return window.location.hash.replace(/^#/, '');
}

// A tiny, dependency-free "router" — reads/writes window.location.hash
// so each tab gets a real, bookmarkable URL and the browser's back/
// forward buttons work, without pulling in a routing library (this
// project stays deliberately dependency-minimal throughout). Route
// *validity* (does the hash match a known tab?) is the caller's job,
// not this hook's — keeps the hook itself generic/reusable.
export function useHashRoute(defaultRoute: string): [string, (route: string) => void] {
  const [route, setRouteState] = useState(() => readHash() || defaultRoute);

  useEffect(() => {
    function handleHashChange() {
      setRouteState(readHash() || defaultRoute);
    }
    window.addEventListener('hashchange', handleHashChange);
    return () => window.removeEventListener('hashchange', handleHashChange);
  }, [defaultRoute]);

  function setRoute(next: string) {
    window.location.hash = next;
    // Setting .hash fires a native hashchange, but asynchronously
    // (task-queued, same as real browsers) — update state directly
    // here rather than waiting to catch our own self-triggered event.
    // The listener above only needs to catch *external* changes
    // (back/forward, a manually-edited URL).
    setRouteState(next);
  }

  return [route, setRoute];
}
