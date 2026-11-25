import { ApiError, request } from "../api.js";
import { dateTime, el, euros, field, formValues, replaceContent } from "../dom.js";
import { flash, go } from "../ui.js";

function decisionForm(dispute) {
  const form = el(
    "form",
    {},
    field(
      "Décision",
      el(
        "select",
        { name: "outcome", required: true },
        el("option", { value: "OUI", text: "OUI" }),
        el("option", { value: "NON", text: "NON" }),
        el("option", { value: "ANNULE", text: "ANNULÉ (remboursement)" }),
      ),
    ),
    field("Justification (20 caractères minimum)", el("textarea", { name: "justification", required: true, minlength: 20, maxlength: 1000 })),
    el("button", { type: "submit", text: "Trancher" }),
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const values = formValues(form);
    try {
      await request("POST", `/api/moderation/bets/${dispute.id}/decision`, {
        outcome: values.outcome,
        justification: values.justification,
      });
      flash("Décision enregistrée.");
      go("#/moderation");
    } catch (error) {
      flash(error instanceof ApiError ? error.message : "Décision impossible.", true);
    }
  });
  return form;
}

function disputeCard(dispute) {
  const contests = dispute.contests.map((contest) =>
    el("li", {}, el("strong", { text: contest.contester }), el("span", { className: "muted", text: ` · ${dateTime(contest.created_at)}` }), el("p", { text: contest.reason })),
  );
  return el(
    "section",
    { className: "card" },
    el("h3", { text: dispute.title }),
    el("p", { className: "muted", text: `Bookie : ${dispute.bookie} · résultat proposé : ${dispute.proposed_outcome || "aucun"}` }),
    dispute.proposal_note ? el("p", { text: `Justificatif : ${dispute.proposal_note}` }) : null,
    el("p", { text: `OUI ${euros(dispute.totals.OUI)} · NON ${euros(dispute.totals.NON)}` }),
    contests.length ? el("ul", { className: "list" }, contests) : el("p", { className: "muted", text: "Escaladé faute de résultat." }),
    decisionForm(dispute),
  );
}

export async function renderModeration(container) {
  const page = await request("GET", "/api/moderation/disputes");
  replaceContent(
    container,
    el("section", { className: "card" }, el("h2", { text: "Litiges en attente" }), el("p", { className: "muted", text: `${page.items.length} pari(s) à trancher.` })),
    page.items.map(disputeCard),
  );
}
