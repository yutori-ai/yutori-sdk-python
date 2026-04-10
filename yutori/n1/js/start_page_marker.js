() => {
  const state = history.state && typeof history.state === "object" ? history.state : {};
  history.replaceState({ ...state, isYutoriStartMarker: true }, document.title);
  return true;
}
