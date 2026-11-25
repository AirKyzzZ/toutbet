import { ApiError, hasToken, request, setToken } from "./api.js";
import { renderLogin, renderRegister } from "./views/auth.js";
import { renderBet, renderBetList, renderNewBet } from "./views/bets.js";
import { renderModeration } from "./views/moderation.js";
import { renderWallet } from "./views/wallet.js";
import { flash, go } from "./ui.js";

const view = document.getElementById("view");
const nav = document.getElementById("nav");
const moderationLink = document.getElementById("nav-moderation");
const degradedBanner = document.getElementById("degraded");

let pendingInvitation = null;
let currentUser = null;

function captureInvitation() {
  const match = window.location.hash.match(/^#\/invite\/([A-Za-z0-9_-]{43})$/);
  if (!match) {
    return;
  }
  pendingInvitation = match[1];
  window.history.replaceState(null, "", "#/bets");
}

async function acceptPendingInvitation() {
  if (!pendingInvitation || !hasToken()) {
    return;
  }
  const token = pendingInvitation;
  pendingInvitation = null;
  try {
    const result = await request("POST", "/api/invitations/accept", { token });
    flash("Invitation acceptée.");
    go(`#/bets/${result.bet_id}`);
  } catch (error) {
    flash(error instanceof ApiError ? error.message : "Invitation impossible.", true);
  }
}

async function loadCurrentUser() {
  if (!hasToken()) {
    currentUser = null;
    return;
  }
  try {
    currentUser = await request("GET", "/api/me");
  } catch {
    currentUser = null;
  }
}

function betCreationOpen() {
  return !currentUser || currentUser.bet_creation_enabled !== false;
}

const routes = [
  [/^#\/login$/, () => renderLogin(view, onLogin)],
  [/^#\/register$/, () => renderRegister(view)],
  [/^#\/wallet$/, () => renderWallet(view, currentUser)],
  [/^#\/bets\/new$/, () => renderNewBet(view, betCreationOpen())],
  [/^#\/bets\/([0-9a-f-]{36})$/, (match) => renderBet(view, match[1])],
  [/^#\/moderation$/, () => renderModeration(view)],
  [/^#\/bets$/, () => renderBetList(view, betCreationOpen())],
];

async function render() {
  captureInvitation();
  const hash = window.location.hash || "#/bets";
  const isPublic = hash === "#/login" || hash === "#/register";
  if (!hasToken() && !isPublic) {
    go("#/login");
    return;
  }
  if (hasToken() && !currentUser) {
    await loadCurrentUser();
  }
  nav.hidden = !hasToken();
  moderationLink.hidden = !(currentUser && currentUser.role === "moderator");
  degradedBanner.hidden = !hasToken() || betCreationOpen();
  for (const [pattern, handler] of routes) {
    const match = hash.match(pattern);
    if (match) {
      try {
        await handler(match);
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) {
          currentUser = null;
          go("#/login");
          return;
        }
        flash(error instanceof ApiError ? error.message : "Erreur inattendue.", true);
      }
      return;
    }
  }
  go("#/bets");
}

async function onLogin() {
  await loadCurrentUser();
  if (pendingInvitation) {
    await acceptPendingInvitation();
    return;
  }
  go(currentUser && currentUser.role === "moderator" ? "#/moderation" : "#/bets");
}

document.getElementById("logout").addEventListener("click", async () => {
  try {
    await request("POST", "/api/auth/logout");
  } catch {
    flash("Session déjà expirée.");
  }
  setToken(null);
  currentUser = null;
  go("#/login");
});

window.addEventListener("hashchange", () => {
  flash("");
  render();
});

captureInvitation();
render().then(acceptPendingInvitation);
