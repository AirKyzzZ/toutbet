import { ApiError, request } from "../api.js";
import { centsFromEuros, dateTime, el, euros, field, formValues, replaceContent, safeExternalUrl } from "../dom.js";
import { flash, go } from "../ui.js";

const ENTRY_LABELS = {
  deposit: "Dépôt",
  stake: "Mise",
  refund: "Remboursement",
  payout: "Gain",
  bookie_fee: "Commission bookie",
  withdrawal: "Retrait",
  withdrawal_reversal: "Retrait annulé",
};

function redirectTo(url) {
  const safe = safeExternalUrl(url);
  if (!safe) {
    flash("Adresse de redirection refusée.", true);
    return;
  }
  window.location.assign(safe);
}

function amountForm(label, minimum, maximum, extraFields, submitLabel, onSubmit) {
  const form = el(
    "form",
    {},
    field(label, el("input", { name: "amount", inputmode: "decimal", required: true, placeholder: `${minimum} à ${maximum}` })),
    ...extraFields,
    el("button", { type: "submit", text: submitLabel }),
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const values = formValues(form);
    const cents = centsFromEuros(values.amount);
    if (cents === null) {
      flash("Montant invalide.", true);
      return;
    }
    try {
      await onSubmit(cents, values);
      form.reset();
    } catch (error) {
      flash(error instanceof ApiError ? error.message : "Opération impossible.", true);
    }
  });
  return form;
}

export async function renderWallet(container, user) {
  const wallet = await request("GET", "/api/wallet");
  const deposit = amountForm("Montant à déposer (€)", "5", "500", [], "Payer chez le PSP", async (cents) => {
    const result = await request("POST", "/api/deposits", { amount_cents: cents }, { idempotent: true });
    redirectTo(result.redirect_url);
  });
  const withdraw = amountForm(
    "Montant à retirer (€)",
    "10",
    "1000",
    [field("Mot de passe", el("input", { name: "password", type: "password", required: true, maxlength: 128 }))],
    "Retirer vers mon compte vérifié",
    async (cents, values) => {
      await request("POST", "/api/withdrawals", { amount_cents: cents, password: values.password }, { idempotent: true });
      flash("Virement demandé.");
      go("#/wallet");
    },
  );
  const kyc = el("button", { type: "button", className: "secondary", text: "Vérifier mon identité" });
  kyc.addEventListener("click", async () => {
    try {
      const result = await request("POST", "/api/kyc/start");
      redirectTo(result.redirect_url);
    } catch (error) {
      flash(error instanceof ApiError ? error.message : "Vérification impossible.", true);
    }
  });
  const entries = wallet.entries.map((entry) =>
    el(
      "li",
      { className: "row" },
      el("span", {}, ENTRY_LABELS[entry.entry_type] || entry.entry_type, el("br"), el("span", { className: "muted", text: dateTime(entry.created_at) })),
      el("strong", { text: `${entry.direction === "credit" ? "+" : "-"}${euros(entry.amount_cents)}` }),
    ),
  );
  const verified = user && user.kyc_status === "verified";
  replaceContent(
    container,
    el("section", { className: "card" }, el("h2", { text: "Mon solde" }), el("p", { className: "amount", text: euros(wallet.balance_cents) })),
    el("section", { className: "card" }, el("h3", { text: "Déposer" }), el("p", { className: "muted", text: "La carte est saisie chez le prestataire de paiement, jamais chez nous." }), deposit),
    el(
      "section",
      { className: "card" },
      el("h3", { text: "Retirer" }),
      verified ? withdraw : el("div", {}, el("p", { className: "muted", text: "Un retrait demande une identité vérifiée chez le prestataire." }), kyc),
    ),
    el("section", { className: "card" }, el("h3", { text: "Historique" }), entries.length ? el("ul", { className: "list" }, entries) : el("p", { className: "muted", text: "Aucun mouvement." })),
  );
}
