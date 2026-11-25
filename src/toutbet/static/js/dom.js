export function el(tag, attributes = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (value === false || value === null || value === undefined) {
      continue;
    }
    if (name === "text") {
      node.textContent = String(value);
    } else if (name === "className") {
      node.className = value;
    } else if (name.startsWith("on")) {
      throw new Error("use addEventListener instead of inline handlers");
    } else {
      node.setAttribute(name, value === true ? "" : String(value));
    }
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) {
      continue;
    }
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function replaceContent(container, ...children) {
  container.replaceChildren(...children.flat().filter(Boolean));
}

export function euros(cents) {
  return new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR" }).format(cents / 100);
}

export function centsFromEuros(value) {
  const normalized = String(value).trim().replace(",", ".");
  if (!/^\d+(\.\d{1,2})?$/.test(normalized)) {
    return null;
  }
  const [whole, fraction = ""] = normalized.split(".");
  return Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
}

export function dateTime(iso) {
  if (!iso) {
    return "";
  }
  return new Intl.DateTimeFormat("fr-FR", { dateStyle: "short", timeStyle: "short" }).format(
    new Date(iso),
  );
}

export function field(label, input) {
  return el("label", {}, el("span", { text: label }), input);
}

export function formValues(form) {
  return Object.fromEntries(new FormData(form).entries());
}

export function safeExternalUrl(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
  } catch {
    return null;
  }
}
