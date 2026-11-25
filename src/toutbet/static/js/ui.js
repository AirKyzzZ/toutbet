const flashBox = document.getElementById("flash");

export function flash(message, isError = false) {
  flashBox.textContent = message;
  flashBox.classList.toggle("error", isError);
  flashBox.hidden = !message;
}

export function go(route) {
  if (window.location.hash === route) {
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  } else {
    window.location.hash = route;
  }
}
