/* The panel. One file, no build step, no libraries — the whole thing is a
   thin skin over /api, and every control writes the moment it loses focus. */

const $ = (sel, root = document) => root.querySelector(sel);

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function api(path, body) {
  const opts = body
    ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
    : {};
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error((await res.text()) || res.statusText);
  return res.json();
}

function toast(text, kind) {
  const box = $("#toast");
  const node = el("div", kind, text);
  box.appendChild(node);
  setTimeout(() => node.remove(), kind === "bad" ? 6000 : 2200);
}

const failed = (err) => toast(String(err.message || err), "bad");

let SECTIONS = [];
let current = "keys";

/* ------------------------------------------------------------------ fields */

function control(field, onSave) {
  const save = (value) => onSave(value);

  if (field.kind === "bool") {
    const node = el("div", "switch" + (field.value ? " on" : ""));
    node.onclick = () => {
      node.classList.toggle("on");
      save(node.classList.contains("on"));
    };
    return node;
  }

  if (field.kind === "select") {
    const node = el("select");
    (field.options || []).forEach(([value, label]) => {
      const opt = el("option", null, label);
      opt.value = value;
      if (String(field.value) === String(value)) opt.selected = true;
      node.appendChild(opt);
    });
    node.onchange = () => save(node.value);
    return node;
  }

  if (field.kind === "range") {
    const wrap = el("div", "pair");
    const from = el("input"), to = el("input");
    from.type = to.type = "number";
    from.value = field.value[0];
    to.value = field.value[1];
    const push = () => save([Number(from.value), Number(to.value)]);
    from.onchange = to.onchange = push;
    wrap.append(from, el("span", null, "—"), to);
    return wrap;
  }

  if (field.kind === "lines") {
    const node = el("textarea");
    node.value = (field.value || []).join("\n");
    node.rows = Math.min(Math.max(field.value.length + 1, 3), 14);
    node.onchange = () => save(node.value);
    return node;
  }

  const node = el("input");
  node.type = field.kind === "int" || field.kind === "float" ? "number" : "text";
  if (field.kind === "float") node.step = "0.01";
  node.value = field.value;
  node.onchange = () => save(node.value);
  return node;
}

function fieldRow(field) {
  const wide = field.kind === "lines";
  const row = el("div", "field" + (wide ? " wide" : ""));
  const name = el("div", "name");
  const label = el("label", null, field.label);

  const overridden = JSON.stringify(field.value) !== JSON.stringify(field.default);
  if (overridden && field.store === "tune") label.appendChild(el("span", "dot"));
  if (field.restart) label.appendChild(el("span", "restart", "после перезапуска"));

  if (overridden && field.store === "tune") {
    const undo = el("button", "reset", "вернуть как было");
    undo.onclick = async () => {
      try {
        await api("/api/reset", { key: field.key });
        toast("вернул значение из кода");
        await open(current);
      } catch (err) { failed(err); }
    };
    label.appendChild(undo);
  }

  name.appendChild(label);
  if (field.hint) name.appendChild(el("div", "hint", field.hint));

  const box = el("div");
  box.appendChild(control(field, async (value) => {
    try {
      const out = await api("/api/set", { key: field.key, value });
      field.value = out.value;
      toast(out.restart ? "сохранено — применится после перезапуска" : "сохранено",
            out.restart ? "warn" : "");
    } catch (err) { failed(err); }
  }));

  row.append(name, box);
  return row;
}

/* -------------------------------------------------------------------- keys */

async function renderKeys(host) {
  const data = await api("/api/keys");
  const card = el("div", "card");

  data.keys.forEach((key) => {
    const row = el("div", "krow");
    const title = el("div");
    title.appendChild(el("div", null, key.label));
    const env = el("div", "env", key.env + " · ");
    env.appendChild(key.live ? el("i", null, "сразу") : el("span", null, "перезапуск"));
    title.appendChild(env);

    const mask = el("div", "mask" + (key.filled ? "" : " empty"),
                    key.filled ? key.mask : "не задан");
    const acts = el("div", "acts");

    if (key.filled) {
      const eye = el("button", "mini", "показать");
      eye.onclick = async () => {
        if (mask.classList.contains("shown")) {
          mask.className = "mask";
          mask.textContent = key.mask;
          eye.textContent = "показать";
          return;
        }
        try {
          const out = await api("/api/keys/reveal", { id: key.id });
          mask.className = "mask shown";
          mask.textContent = out.value;
          eye.textContent = "скрыть";
          setTimeout(() => {
            if (!mask.classList.contains("shown")) return;
            mask.className = "mask";
            mask.textContent = key.mask;
            eye.textContent = "показать";
          }, 20000);
        } catch (err) { failed(err); }
      };
      acts.appendChild(eye);
    }

    const edit = el("button", "mini go", key.filled ? "изменить" : "задать");
    edit.onclick = () => {
      const input = el("input");
      input.type = "text";
      input.placeholder = "вставь ключ и нажми Enter";
      mask.replaceWith(input);
      input.focus();
      const commit = async () => {
        try {
          await api("/api/keys/set", { id: key.id, value: input.value });
          toast("ключ сохранён" + (key.live ? "" : " — применится после перезапуска"),
                key.live ? "" : "warn");
          await open("keys");
        } catch (err) { failed(err); }
      };
      input.onkeydown = (e) => { if (e.key === "Enter") commit(); if (e.key === "Escape") open("keys"); };
    };
    acts.appendChild(edit);

    row.append(title, mask, acts);
    card.appendChild(row);
  });
  host.appendChild(card);

  const open2 = el("div", "card");
  data.open.forEach((item) => {
    const row = el("div", "field");
    const name = el("div", "name");
    name.appendChild(el("label", null, item.label));
    if (item.hint) name.appendChild(el("div", "hint", item.hint));
    const input = el("input");
    input.type = "text";
    input.value = item.value || "";
    input.onchange = async () => {
      try {
        await api("/api/keys/open", { key: item.key, value: input.value });
        toast("сохранено — применится после перезапуска", "warn");
      } catch (err) { failed(err); }
    };
    const box = el("div");
    box.appendChild(input);
    row.append(name, box);
    open2.appendChild(row);
  });
  host.appendChild(open2);
}

/* ----------------------------------------------------------------- prompts */

let promptName = "script";

async function renderPrompts(host) {
  const list = (await api("/api/prompts")).prompts;
  if (!list.some((p) => p.name === promptName)) promptName = list[0].name;

  const tabs = el("div", "tabs");
  list.forEach((item) => {
    const tab = el("button", item.name === promptName ? "on" : "", item.title);
    tab.appendChild(el("i", null, item.size));
    tab.onclick = () => { promptName = item.name; open("prompts"); };
    tabs.appendChild(tab);
  });
  host.appendChild(tabs);

  const one = await api("/api/prompts?name=" + promptName);
  const area = el("textarea", "editor");
  area.value = one.text;
  host.appendChild(area);

  const bar = el("div", "bar");
  const save = el("button", "mini go", "Сохранить");
  save.onclick = async () => {
    try {
      await api("/api/prompts/save", { name: promptName, text: area.value });
      toast("промпт сохранён — применится на следующей генерации");
      open("prompts");
    } catch (err) { failed(err); }
  };
  bar.appendChild(save);

  if (one.backup) {
    const undo = el("button", "mini", "Вернуть предыдущую версию");
    undo.onclick = async () => {
      try {
        const out = await api("/api/prompts/restore", { name: promptName });
        area.value = out.text;
        toast("вернул предыдущую версию");
      } catch (err) { failed(err); }
    };
    bar.appendChild(undo);
  }
  bar.appendChild(el("div", "grow", "копия предыдущей версии лежит рядом файлом .md.bak"));
  host.appendChild(bar);
}

/* ----------------------------------------------------------------- sources */

async function renderSources(host) {
  const rows = (await api("/api/sources")).sources;
  const card = el("div", "card");

  const head = el("div", "src head");
  head.append(el("div", null, ""), el("div", "nm", "источник"),
              el("div", "num", "собрано"), el("div", "num", "болей"),
              el("div", "num", "выхлоп"));
  card.appendChild(head);

  rows.forEach((row) => {
    const line = el("div", "src" + (row.enabled ? "" : " off"));
    const sw = el("div", "switch" + (row.enabled ? " on" : ""));
    sw.onclick = async () => {
      sw.classList.toggle("on");
      try {
        await api("/api/sources/toggle", { id: row.id, enabled: sw.classList.contains("on") });
        line.classList.toggle("off", !sw.classList.contains("on"));
      } catch (err) { failed(err); }
    };
    const name = el("div", "nm");
    name.appendChild(document.createTextNode(row.kind === "reddit" ? "r/" + row.name : row.name));
    if (row.last_error) name.appendChild(el("small", null, "  · " + row.last_error.slice(0, 40)));

    const stored = el("div", "num");
    stored.appendChild(el("b", null, row.stored_total || 0));
    const pains = el("div", "num");
    pains.appendChild(el("b", null, row.pains_total || 0));
    const rate = row.stored_total
      ? Math.round((row.pains_total / row.stored_total) * 100) + "%"
      : "—";
    line.append(sw, name, stored, pains, el("div", "num", rate));
    card.appendChild(line);
  });

  const add = el("div", "add");
  const input = el("input");
  input.type = "text";
  input.placeholder = "добавить сабреддит, например SomebodyMakeThis";
  const go = el("button", "mini go", "Добавить");
  const commit = async () => {
    if (!input.value.trim()) return;
    try {
      await api("/api/sources/add", { name: input.value });
      toast("добавил r/" + input.value.trim());
      open("sources");
    } catch (err) { failed(err); }
  };
  go.onclick = commit;
  input.onkeydown = (e) => { if (e.key === "Enter") commit(); };
  add.append(input, go);
  card.appendChild(add);
  host.appendChild(card);

  host.appendChild(el("div", "note",
    "Удаления нет намеренно: за источником тянутся посты, боли, идеи и сценарии. " +
    "Выключенный источник просто перестаёт опрашиваться, всё собранное остаётся."));
}

/* ------------------------------------------------------------------ render */

async function open(id) {
  current = id;
  window.scrollTo(0, 0);
  const section = SECTIONS.find((s) => s.id === id);
  document.querySelectorAll("nav button").forEach((b) =>
    b.classList.toggle("on", b.dataset.id === id));

  const main = $("#main");
  main.innerHTML = "";
  main.appendChild(el("h1", null, section.title));
  if (section.note) main.appendChild(el("div", "note", section.note));

  try {
    if (section.widget === "keys") await renderKeys(main);
    if (section.widget === "prompts") await renderPrompts(main);
    if (section.widget === "sources") await renderSources(main);
  } catch (err) { failed(err); }

  if (section.fields.length) {
    const card = el("div", "card");
    section.fields.forEach((field) => card.appendChild(fieldRow(field)));
    main.appendChild(card);
  }
}

function renderNav() {
  const nav = $("#nav");
  nav.innerHTML = "";
  SECTIONS.forEach((section) => {
    const button = el("button", section.id === current ? "on" : "", section.title);
    button.dataset.id = section.id;
    if (section.fields.length) button.appendChild(el("span", "n", section.fields.length));
    button.onclick = () => open(section.id);
    nav.appendChild(button);
  });
}

async function status() {
  try {
    const data = await api("/api/status");
    const chips = $("#chips");
    chips.innerHTML = "";
    Object.entries(data.counts).forEach(([label, value]) => {
      const chip = el("div", "chip", label + " ");
      chip.appendChild(el("b", null, value));
      chips.appendChild(chip);
    });
    if (data.credits !== null && data.credits !== undefined) {
      const chip = el("div", "chip live", data.provider + " ");
      chip.appendChild(el("b", null, data.credits.toFixed(1)));
      chips.appendChild(chip);
    }
    if (data.errors.length) {
      const chip = el("div", "chip bad", "ошибок ");
      chip.appendChild(el("b", null, data.errors.length));
      chip.title = data.errors.map((e) => e.kind + ": " + e.error).join("\n\n");
      chips.appendChild(chip);
    }
  } catch (err) { /* the bot may be restarting; the next tick will catch up */ }
}

(async function boot() {
  try {
    SECTIONS = (await api("/api/state")).sections;
  } catch (err) {
    failed(err);
    return;
  }
  renderNav();
  await open(current);
  await status();
  setInterval(status, 20000);
})();
