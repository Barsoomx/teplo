const CLIMATE = 'Air conditioner';
const POWER = 'Power usage';
const OUTDOOR = 'Outdoor temperature';
const DISPLAY = 'Display toggle';
const SEND_DELAY_MS = 700;

const MODES = [
  { value: 'HEAT', label: 'Обогрев', icon: '☀', color: 'var(--heat)' },
  { value: 'COOL', label: 'Холод', icon: '❄', color: 'var(--cool)' },
  { value: 'HEAT_COOL', label: 'Авто', icon: '⟳', color: 'var(--auto)' },
  { value: 'DRY', label: 'Осушение', icon: '💧', color: 'var(--dry)' },
  { value: 'FAN_ONLY', label: 'Вентилятор', icon: '✲', color: 'var(--fan)' },
  { value: 'OFF', label: 'Выкл', icon: '⏻', color: 'var(--off)' },
];
const FANS = [
  { value: 'AUTO', label: 'Авто' },
  { value: 'LOW', label: 'Тихо' },
  { value: 'MEDIUM', label: 'Средне' },
  { value: 'HIGH', label: 'Сильно' },
];
const PRESETS = [
  { value: 'NONE', label: 'Обычный' },
  { value: 'BOOST', label: 'Турбо' },
  { value: 'ECO', label: 'Эко' },
];

const state = {
  mode: null,
  fan: null,
  preset: null,
  room: null,
  target: null,
  min: 17,
  max: 30,
  step: 0.5,
  power: null,
  outdoor: null,
  connected: false,
};
let pendingTarget = null;
let sendTimer = null;
let toastTimer = null;

function number(value) {
  const parsed = parseFloat(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function degrees(value, digits = 1) {
  return value === null ? '—' : `${value.toFixed(digits).replace('.', ',')}°`;
}

function entityUrl(domain, name, action = '') {
  return `/${domain}/${encodeURIComponent(name)}${action ? `/${action}` : ''}`;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function toast(message) {
  ui.toast.textContent = message;
  ui.toast.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ui.toast.classList.remove('show'), 2600);
}

async function post(url) {
  try {
    const response = await fetch(url, { method: 'POST' });
    if (!response.ok) throw new Error(String(response.status));
  } catch (error) {
    toast('Кондиционер не ответил, попробуй ещё раз');
  }
}

function setClimate(params) {
  const query = new URLSearchParams(params).toString();
  return post(`${entityUrl('climate', CLIMATE, 'set')}?${query}`);
}

function chooseMode(mode) {
  state.mode = mode;
  render();
  setClimate({ mode });
}

function chooseFan(fan) {
  state.fan = fan;
  render();
  setClimate({ fan_mode: fan });
}

function choosePreset(preset) {
  state.preset = preset;
  render();
  setClimate({ preset });
}

function nudge(direction) {
  const base = pendingTarget ?? state.target ?? 22;
  const next = Math.min(state.max, Math.max(state.min, base + direction * state.step));
  pendingTarget = Math.round(next / state.step) * state.step;
  render();
  clearTimeout(sendTimer);
  sendTimer = setTimeout(() => {
    const target = pendingTarget;
    pendingTarget = null;
    state.target = target;
    setClimate({ target_temperature: target.toFixed(1) });
  }, SEND_DELAY_MS);
}

function chipGroup(options, onPick, className) {
  const grid = element('div', className);
  const buttons = options.map((option) => {
    const button = element('button', 'chip');
    if (option.icon) button.append(element('span', 'icon', option.icon));
    button.append(document.createTextNode(option.label));
    if (option.color) button.style.setProperty('--chip', option.color);
    button.addEventListener('click', () => onPick(option.value));
    grid.append(button);
    return { option, button };
  });
  return { grid, buttons };
}

function card(labelText, content) {
  const box = element('section', 'card');
  if (labelText) box.append(element('div', 'label', labelText));
  box.append(content);
  return box;
}

function build() {
  const head = document.head;
  const viewport = element('meta');
  viewport.name = 'viewport';
  viewport.content = 'width=device-width, initial-scale=1, viewport-fit=cover';
  head.append(viewport);
  const theme = element('meta');
  theme.name = 'theme-color';
  theme.content = '#0f1419';
  head.append(theme);
  document.title = 'Кондиционер';

  const root = element('main', 'remote');
  const top = element('header', 'top');
  const titles = element('div');
  titles.append(element('h1', '', 'Кондиционер'));
  titles.append(element('small', '', 'Спальня'));
  const dot = element('span', 'dot');
  top.append(titles, dot);

  const dial = element('section', 'card dial');
  const room = element('div', 'room');
  const targetRow = element('div', 'target-row');
  const minus = element('button', 'step', '−');
  const target = element('div', 'target', '—');
  const plus = element('button', 'step', '+');
  minus.addEventListener('click', () => nudge(-1));
  plus.addEventListener('click', () => nudge(1));
  targetRow.append(minus, target, plus);
  const caption = element('div', 'caption');
  dial.append(room, targetRow, caption);

  const modes = chipGroup(MODES, chooseMode, 'grid');
  const fans = chipGroup(FANS, chooseFan, 'grid four');
  const presets = chipGroup(PRESETS, choosePreset, 'grid');

  const display = element('button', 'chip wide', 'Дисплей вкл/выкл');
  display.addEventListener('click', () => {
    post(entityUrl('button', DISPLAY, 'press'));
    toast('Переключаю дисплей');
  });

  const facts = element('div', 'facts');
  const power = element('span');
  const outdoor = element('span');
  facts.append(power, outdoor);

  root.append(
    top,
    dial,
    card('Режим', modes.grid),
    card('Вентилятор', fans.grid),
    card('Мощность', presets.grid),
    card('', display),
    card('', facts),
  );
  const toastBox = element('div', 'toast');
  document.body.prepend(root);
  document.body.append(toastBox);

  return { dot, dial, room, minus, plus, target, caption, modes, fans, presets, power, outdoor, toast: toastBox };
}

const ui = build();

function markActive(group, value, disabled) {
  for (const { option, button } of group.buttons) {
    button.classList.toggle('active', option.value === value);
    button.disabled = disabled;
  }
}

function render() {
  const mode = MODES.find((item) => item.value === state.mode);
  const off = state.mode === 'OFF';
  document.documentElement.style.setProperty('--accent', mode ? mode.color : 'var(--off)');

  ui.dot.classList.toggle('on', state.connected);
  ui.room.innerHTML = '';
  ui.room.append('В комнате ');
  ui.room.append(element('b', '', degrees(state.room)));

  const shown = pendingTarget ?? state.target;
  ui.target.textContent = degrees(shown);
  ui.target.classList.toggle('idle', off);
  ui.minus.disabled = shown !== null && shown <= state.min;
  ui.plus.disabled = shown !== null && shown >= state.max;
  ui.caption.textContent = off ? `Выключен · включится на ${degrees(shown)}` : `${mode ? mode.label : '…'} · цель`;

  markActive(ui.modes, state.mode, false);
  markActive(ui.fans, state.fan, off);
  markActive(ui.presets, state.preset, off);

  ui.power.innerHTML = '';
  ui.power.append('Сейчас ');
  ui.power.append(element('b', '', state.power === null ? '—' : `${Math.round(state.power)} Вт`));
  ui.outdoor.innerHTML = '';
  ui.outdoor.append('На улице ');
  ui.outdoor.append(element('b', '', degrees(state.outdoor, 0)));
}

function applyClimate(data) {
  if (data.mode) state.mode = data.mode;
  if (data.fan_mode) state.fan = data.fan_mode;
  if (data.preset) state.preset = data.preset;
  if (data.current_temperature !== undefined) state.room = number(data.current_temperature);
  if (data.target_temperature !== undefined) state.target = number(data.target_temperature);
  if (data.min_temp !== undefined) state.min = number(data.min_temp) ?? state.min;
  if (data.max_temp !== undefined) state.max = number(data.max_temp) ?? state.max;
  if (data.step !== undefined) state.step = number(data.step) ?? state.step;
}

function applyOutdoor(value) {
  const reading = number(value);
  state.outdoor = reading !== null && reading > -50 && reading < 60 ? reading : null;
}

function apply(data) {
  if (data.id === `climate/${CLIMATE}`) applyClimate(data);
  else if (data.id === `sensor/${POWER}`) state.power = number(data.value);
  else if (data.id === `sensor/${OUTDOOR}`) applyOutdoor(data.value);
  else return;
  render();
}

async function load(domain, name) {
  try {
    const response = await fetch(entityUrl(domain, name));
    if (response.ok) apply(await response.json());
  } catch (error) {
    state.connected = false;
    render();
  }
}

function listen() {
  const events = new EventSource('/events');
  events.addEventListener('open', () => {
    state.connected = true;
    render();
  });
  events.addEventListener('error', () => {
    state.connected = false;
    render();
  });
  events.addEventListener('state', (event) => {
    try {
      apply(JSON.parse(event.data));
    } catch (error) {
      return;
    }
  });
}

render();
load('climate', CLIMATE);
load('sensor', POWER);
load('sensor', OUTDOOR);
listen();
