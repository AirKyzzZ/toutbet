import { ApiError, request } from "../api.js";
import { centsFromEuros, dateTime, el, euros, field, formValues, replaceContent } from "../dom.js";
import { flash, go } from "../ui.js";

const STATUS_LABELS = {
  open: "Ouvert",
  closed: "Clos",
  result_proposed: "Résultat proposé",
  disputed: "En litige",
  settled: "Réglé",
  cancelled: "Annulé",
};

function reportError(error) {
  flash(error instanceof ApiError ? error.message : "Action impossible.", true);
}

function actionForm(fields, label, onSubmit, className = "") {
  const form = el("form", {}, ...fields, el("button", { type: "submit", text: label, className }));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await onSubmit(formValues(form));
    } catch (error) {
      reportError(error);
    }
  });
  return form;
}

function outcomeSelect(name, withCancel = false) {
  const options = [el("option", { value: "OUI", text: "OUI" }), el("option", { value: "NON", text: "NON" })];
  if (withCancel) {
    options.push(el("option", { value: "ANNULE", text: "ANNULÉ" }));
  }
  return el("select", { name, required: true }, options);
}

export async function renderBetList(container) {
  const page = await request("GET", "/api/bets");
  const items = page.items.map((bet) =>
    el(
      "li",
      {},
      el("a", { href: `#/bets/${bet.id}`, text: bet.title }),
      el("div", { className: "row muted" }, el("span", { text: STATUS_LABELS[bet.status] || bet.status }), el("span", { text: `OUI ${euros(bet.totals.OUI)} · NON ${euros(bet.totals.NON)}` })),
    ),
  );
  replaceContent(
    container,
    el("section", { className: "card" }, el("h2", { text: "Mes paris" }), el("a", { href: "#/bets/new", text: "Créer un pari" })),
    el("section", { className: "card" }, items.length ? el("ul", { className: "list" }, items) : el("p", { className: "muted", text: "Aucun pari pour l'instant. Créez-en un ou utilisez un lien d'invitation." })),
  );
}

export function renderNewBet(container) {
  const form = actionForm(
    [
      field("Titre", el("input", { name: "title", required: true, minlength: 5, maxlength: 140 })),
      field("Description", el("textarea", { name: "description", maxlength: 1000 })),
      field("Début de l'événement", el("input", { name: "event_starts_at", type: "datetime-local", required: true })),
      field("Clôture des mises", el("input", { name: "closes_at", type: "datetime-local", required: true })),
      field("Commission bookie (%)", el("input", { name: "commission", inputmode: "decimal", value: "0", required: true })),
      field("Mise minimum (€, optionnel)", el("input", { name: "min_stake", inputmode: "decimal" })),
    ],
    "Créer le pari",
    async (values) => {
      const percent = Number(String(values.commission).replace(",", "."));
      const minStake = values.min_stake ? centsFromEuros(values.min_stake) : null;
      const body = {
        title: values.title,
        description: values.description || null,
        event_starts_at: new Date(values.event_starts_at).toISOString(),
        closes_at: new Date(values.closes_at).toISOString(),
        bookie_commission_bps: Math.round(percent * 100),
      };
      if (minStake !== null) {
        body.min_stake_cents = minStake;
      }
      const bet = await request("POST", "/api/bets", body);
      go(`#/bets/${bet.id}`);
    },
  );
  replaceContent(container, el("section", { className: "card" }, el("h2", { text: "Nouveau pari" }), form));
}

function bookieActions(bet) {
  const actions = [];
  if (bet.status === "open") {
    const output = el("p", { className: "token", hidden: true });
    const invite = el("button", { type: "button", className: "secondary", text: "Créer un lien d'invitation" });
    invite.addEventListener("click", async () => {
      try {
        const result = await request("POST", `/api/bets/${bet.id}/invitations`);
        output.textContent = result.invitation_url;
        output.hidden = false;
      } catch (error) {
        reportError(error);
      }
    });
    const cancel = el("button", { type: "button", className: "danger", text: "Annuler le pari" });
    cancel.addEventListener("click", async () => {
      try {
        await request("POST", `/api/bets/${bet.id}/cancel`);
        go(`#/bets/${bet.id}`);
      } catch (error) {
        reportError(error);
      }
    });
    actions.push(el("div", { className: "actions" }, invite, cancel), output);
  }
  if (bet.status === "closed") {
    actions.push(
      actionForm(
        [field("Résultat", outcomeSelect("outcome", true)), field("Justificatif (texte)", el("textarea", { name: "note", maxlength: 500 }))],
        "Proposer le résultat",
        async (values) => {
          await request("POST", `/api/bets/${bet.id}/result`, { outcome: values.outcome, note: values.note || null });
          go(`#/bets/${bet.id}`);
        },
      ),
    );
  }
  return actions;
}

function participantActions(bet) {
  const actions = [];
  if (bet.status === "open" && !bet.my_stake) {
    actions.push(
      actionForm(
        [field("Issue", outcomeSelect("outcome")), field("Mise (€)", el("input", { name: "amount", inputmode: "decimal", required: true }))],
        "Miser",
        async (values) => {
          const cents = centsFromEuros(values.amount);
          if (cents === null) {
            flash("Montant invalide.", true);
            return;
          }
          const result = await request("POST", `/api/bets/${bet.id}/stakes`, { outcome: values.outcome, amount_cents: cents }, { idempotent: true });
          flash(`Mise enregistrée. Reçu signé : ${result.receipt.signature.slice(0, 16)}…`);
          go(`#/bets/${bet.id}`);
        },
      ),
    );
  }
  if (bet.status === "result_proposed" && bet.my_stake && !bet.my_contested) {
    actions.push(
      actionForm(
        [field("Motif de contestation", el("textarea", { name: "reason", required: true, minlength: 10, maxlength: 500 }))],
        "Contester le résultat",
        async (values) => {
          await request("POST", `/api/bets/${bet.id}/contests`, { reason: values.reason });
          go(`#/bets/${bet.id}`);
        },
        "danger",
      ),
    );
  }
  return actions;
}

export async function renderBet(container, betId) {
  const bet = await request("GET", `/api/bets/${betId}`);
  const details = el(
    "ul",
    { className: "list" },
    el("li", { className: "row" }, el("span", { text: "Bookie" }), el("strong", { text: bet.bookie })),
    el("li", { className: "row" }, el("span", { text: "Statut" }), el("span", { className: "badge", text: STATUS_LABELS[bet.status] || bet.status })),
    el("li", { className: "row" }, el("span", { text: "Clôture" }), el("span", { text: dateTime(bet.closes_at) })),
    el("li", { className: "row" }, el("span", { text: "Événement" }), el("span", { text: dateTime(bet.event_starts_at) })),
    el("li", { className: "row" }, el("span", { text: "Total OUI / NON" }), el("span", { text: `${euros(bet.totals.OUI)} / ${euros(bet.totals.NON)}` })),
    el("li", { className: "row" }, el("span", { text: "Commissions" }), el("span", { text: `bookie ${bet.bookie_commission_bps / 100} %, plateforme ${bet.platform_commission_bps / 100} %` })),
    bet.min_stake_cents ? el("li", { className: "row" }, el("span", { text: "Mise minimum" }), el("span", { text: euros(bet.min_stake_cents) })) : null,
    bet.my_stake ? el("li", { className: "row" }, el("span", { text: "Ma mise" }), el("strong", { text: `${bet.my_stake.outcome} · ${euros(bet.my_stake.amount_cents)}` })) : null,
    bet.proposed_outcome ? el("li", { className: "row" }, el("span", { text: "Résultat proposé" }), el("strong", { text: bet.proposed_outcome })) : null,
    bet.contest_deadline ? el("li", { className: "row" }, el("span", { text: "Contestation jusqu'au" }), el("span", { text: dateTime(bet.contest_deadline) })) : null,
    bet.final_outcome ? el("li", { className: "row" }, el("span", { text: "Résultat final" }), el("strong", { text: bet.final_outcome })) : null,
  );
  const decision = bet.decision
    ? el("section", { className: "card" }, el("h3", { text: "Décision du modérateur" }), el("p", { text: `${bet.decision.moderator} : ${bet.decision.outcome}` }), el("p", { className: "muted", text: bet.decision.justification }))
    : null;
  const actions = bet.my_role === "bookie" ? bookieActions(bet) : participantActions(bet);
  replaceContent(
    container,
    el(
      "section",
      { className: "card" },
      el("h2", { text: bet.title }),
      bet.description ? el("p", { text: bet.description }) : null,
      bet.proposal_note ? el("p", { className: "muted", text: `Justificatif : ${bet.proposal_note}` }) : null,
      details,
    ),
    el("section", { className: "card" }, el("h3", { text: "Participants" }), el("p", { text: bet.participants.join(", ") || "Personne pour l'instant." })),
    decision,
    actions.length ? el("section", { className: "card" }, ...actions) : null,
  );
}
