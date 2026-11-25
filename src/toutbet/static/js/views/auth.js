import { ApiError, request, setToken } from "../api.js";
import { el, field, formValues, replaceContent } from "../dom.js";
import { flash, go } from "../ui.js";

export function renderLogin(container, onLogin) {
  const form = el(
    "form",
    {},
    field("Email", el("input", { name: "email", type: "email", required: true, autocomplete: "username" })),
    field(
      "Mot de passe",
      el("input", {
        name: "password",
        type: "password",
        required: true,
        maxlength: 128,
        autocomplete: "current-password",
      }),
    ),
    el("button", { type: "submit", text: "Se connecter" }),
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const values = formValues(form);
    try {
      const result = await request("POST", "/api/auth/login", {
        email: values.email,
        password: values.password,
      });
      setToken(result.access_token);
      form.reset();
      await onLogin();
    } catch (error) {
      flash(error instanceof ApiError ? error.message : "Connexion impossible.", true);
    }
  });
  replaceContent(
    container,
    el(
      "section",
      { className: "card" },
      el("h2", { text: "Connexion" }),
      form,
      el("p", { className: "muted" }, "Pas encore de compte ? ", el("a", { href: "#/register", text: "Créer un compte" })),
    ),
  );
}

export function renderRegister(container) {
  const form = el(
    "form",
    {},
    field("Email", el("input", { name: "email", type: "email", required: true, maxlength: 254 })),
    field(
      "Pseudonyme",
      el("input", { name: "display_name", required: true, minlength: 3, maxlength: 30, pattern: "[A-Za-z0-9_\\-]+" }),
    ),
    field(
      "Mot de passe (12 caractères minimum)",
      el("input", {
        name: "password",
        type: "password",
        required: true,
        minlength: 12,
        maxlength: 128,
        autocomplete: "new-password",
      }),
    ),
    field("Date de naissance", el("input", { name: "birth_date", type: "date", required: true })),
    el("button", { type: "submit", text: "Créer mon compte" }),
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await request("POST", "/api/auth/register", formValues(form));
      form.reset();
      flash("Demande enregistrée. Vous pouvez vous connecter.");
      go("#/login");
    } catch (error) {
      flash(error instanceof ApiError ? error.message : "Inscription impossible.", true);
    }
  });
  replaceContent(
    container,
    el(
      "section",
      { className: "card" },
      el("h2", { text: "Créer un compte" }),
      el("p", { className: "muted", text: "Réservé aux personnes majeures." }),
      form,
    ),
  );
}
