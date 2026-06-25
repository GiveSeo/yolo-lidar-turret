// AI 자동 조준 시스템 — 프론트엔드 로직
// 영상은 /video_feed (MJPEG) 로 <img> 에 표시되고, 상태는 /ws WebSocket 으로 수신한다.
// 표적 선택은 영상 클릭/탭 -> 정규화 좌표로 /api/select_at 호출.

const $ = (id) => document.getElementById(id);
const video = $("video");
let lastState = null;

// --- 영상 클릭/탭으로 표적 선택 ---
function pickFromEvent(e) {
  const rect = video.getBoundingClientRect();
  const pt = e.touches ? e.touches[0] : e;
  // object-fit: contain 보정: 실제 그려진 영상 영역 기준 정규화
  const fw = lastState?.frame_width || 0;
  const fh = lastState?.frame_height || 0;
  let x = (pt.clientX - rect.left) / rect.width;
  let y = (pt.clientY - rect.top) / rect.height;

  if (fw && fh) {
    const boxRatio = rect.width / rect.height;
    const imgRatio = fw / fh;
    if (imgRatio > boxRatio) {
      // 좌우 꽉 참, 상하 레터박스
      const drawnH = rect.width / imgRatio;
      const offY = (rect.height - drawnH) / 2;
      y = (pt.clientY - rect.top - offY) / drawnH;
    } else {
      const drawnW = rect.height * imgRatio;
      const offX = (rect.width - drawnW) / 2;
      x = (pt.clientX - rect.left - offX) / drawnW;
    }
  }
  if (x < 0 || x > 1 || y < 0 || y > 1) return;
  post("/api/select_at", { x, y, normalized: true });
}

video.addEventListener("click", pickFromEvent);
video.addEventListener("touchstart", (e) => { e.preventDefault(); pickFromEvent(e); }, { passive: false });

// --- 버튼 ---
$("btn-engage").addEventListener("click", () => post("/api/engage"));
$("btn-aim").addEventListener("click", () => post("/api/engage", { aim_only: true }));
$("btn-clear").addEventListener("click", () => post("/api/clear_selection"));
$("btn-clear-result").addEventListener("click", () => post("/api/clear_result"));
$("btn-hit").addEventListener("click", () => post("/api/hit", { source: "ui-test" }));
$("btn-home").addEventListener("click", async () => {
  const r = await postJson("/api/home");
  $("cannon-msg").textContent = r && r.ok
    ? `대포 초기화: pan=${r.pan}° tilt=${r.tilt}° (trigger 0)`
    : `초기화 실패: ${r ? r.reason || "오류" : "오류"}`;
});
$("btn-reset-bias").addEventListener("click", async () => {
  const r = await postJson("/api/feedback/reset");
  $("cannon-msg").textContent = r && r.ok
    ? "보정 초기화됨 (tilt 0° / pan 0°)"
    : `보정 초기화 실패: ${r ? r.reason || "오류" : "오류"}`;
});

// --- 착탄 피드백 ---
let fbDrop = "none";
let fbSide = "none";
document.querySelectorAll(".fb-drop").forEach((b) => {
  b.addEventListener("click", () => {
    fbDrop = b.dataset.drop;
    document.querySelectorAll(".fb-drop").forEach((x) => x.classList.remove("sel"));
    b.classList.add("sel");
  });
});
document.querySelectorAll(".fb-side").forEach((b) => {
  b.addEventListener("click", () => {
    fbSide = b.dataset.side;
    document.querySelectorAll(".fb-side").forEach((x) => x.classList.remove("sel"));
    b.classList.add("sel");
  });
});
$("fb-submit").addEventListener("click", async () => {
  const cm = parseFloat($("fb-cm").value) || 0;
  const sideCm = parseFloat($("fb-side-cm").value) || 0;
  const result = (fbDrop === "none" && fbSide === "none") ? "hit" : "miss";
  const r = await postJson("/api/feedback",
    { result, drop: fbDrop, error_cm: cm, side: fbSide, side_cm: sideCm });
  $("fb-msg").textContent = r && r.ok
    ? `보정 적용: tilt=${r.tilt_bias}°(잔차 ${r.residual}°) · pan=${r.pan_bias}°(잔차 ${r.pan_residual}°) · 누적 ${r.shots_count}발`
    : `적용 실패: ${r ? r.reason || "오류" : "오류"}`;
});

// --- RC 조종 (HAT: DC 전후진 + 서보 좌우 조향) ---
// 버튼을 누르고 있는 동안 이동/조향하고, 떼면 정지/중립으로 돌아가는 "조이스틱" 방식.
// 입력 상태(ctl)를 단일 진실로 두고, 버튼·키보드가 같은 상태를 갱신 -> apply() 가 반영.
const motorMsg = (t) => { const e = $("motor-msg"); if (e) e.textContent = t; };
const SERVO_CENTER = 350;             // 서보 중립(직진) PWM 값
const ctl = { up: false, down: false, left: false, right: false };
let lastDcDir = null, lastServoVal = null;

const dcSpeed = () => parseInt($("dc-speed").value) || 0;
const steerAmt = () => parseInt($("steer-amt").value) || 0;
const steerVal = (dir) => {
  let sign = dir === "right" ? 1 : -1;
  if ($("steer-invert").checked) sign = -sign;   // 좌우가 반대로 꺾이면 체크
  return Math.max(150, Math.min(600, SERVO_CENTER + sign * steerAmt()));
};

function sendDc(dir) {
  const speed = dir === "stop" ? 0 : dcSpeed();
  postJson("/api/motor/dc", { dir, speed }).then((r) => {
    motorMsg(r && r.ok ? `DC ${dir}${dir === "stop" ? "" : " · 속도 " + speed}`
                       : `DC 실패: ${r ? r.reason || "오류" : "Pi 미연결"}`);
  });
}
function sendServo(val) {
  postJson("/api/motor/servo", { val }).then((r) => {
    motorMsg(r && r.ok ? `서보 ${val}` : `서보 실패: ${r ? r.reason || "오류" : "Pi 미연결"}`);
  });
}
function setActive(id, on) { const e = $(id); if (e) e.classList.toggle("active", on); }

// 현재 입력 상태를 모터/서보 명령으로 반영(직전과 같으면 전송 생략)
function apply() {
  let dir = "stop";
  if (ctl.up && !ctl.down) dir = "fwd";
  else if (ctl.down && !ctl.up) dir = "back";
  if (dir !== lastDcDir) { lastDcDir = dir; sendDc(dir); }

  let val = SERVO_CENTER;
  if (ctl.left && !ctl.right) val = steerVal("left");
  else if (ctl.right && !ctl.left) val = steerVal("right");
  if (val !== lastServoVal) { lastServoVal = val; sendServo(val); }

  setActive("rc-up", ctl.up); setActive("rc-down", ctl.down);
  setActive("rc-left", ctl.left); setActive("rc-right", ctl.right);
}

$("dc-speed").addEventListener("input", () => {
  $("dc-speed-v").textContent = $("dc-speed").value;
  if (lastDcDir === "fwd" || lastDcDir === "back") sendDc(lastDcDir); // 주행 중 속도 즉시 반영
});
$("steer-amt").addEventListener("input", () => {
  $("steer-amt-v").textContent = $("steer-amt").value;
  lastServoVal = null; apply();   // 조향 중이면 폭 즉시 반영
});

// 버튼: 누르는 동안 동작, 떼거나 벗어나면 해제(안전)
function bindHold(id, key) {
  const el = $(id);
  if (!el) return;
  const down = (e) => { e.preventDefault(); ctl[key] = true; apply(); };
  const up = (e) => { e.preventDefault(); ctl[key] = false; apply(); };
  el.addEventListener("pointerdown", down);
  el.addEventListener("pointerup", up);
  el.addEventListener("pointerleave", up);
  el.addEventListener("pointercancel", up);
}
bindHold("rc-up", "up");
bindHold("rc-down", "down");
bindHold("rc-left", "left");
bindHold("rc-right", "right");
$("rc-stop").addEventListener("click", () => {
  ctl.up = ctl.down = ctl.left = ctl.right = false; apply();
});

// 키보드 조종 (WASD / 방향키), 자동반복 무시
const KEY = {
  KeyW: "up", ArrowUp: "up", KeyS: "down", ArrowDown: "down",
  KeyA: "left", ArrowLeft: "left", KeyD: "right", ArrowRight: "right",
};
window.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT") return;            // 슬라이더/입력창 포커스면 무시
  if (e.code === "Space") { e.preventDefault(); ctl.up = ctl.down = ctl.left = ctl.right = false; apply(); return; }
  const k = KEY[e.code];
  if (!k) return;
  e.preventDefault();
  if (ctl[k]) return;                                  // 키 자동반복 무시
  ctl[k] = true; apply();
});
window.addEventListener("keyup", (e) => {
  const k = KEY[e.code];
  if (!k) return;
  ctl[k] = false; apply();
});

async function postJson(url, body) {
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return await res.json();
  } catch (e) { console.warn("postJson 실패", url, e); return null; }
}

async function post(url, body) {
  try {
    await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : "{}",
    });
  } catch (e) { console.warn("post 실패", url, e); }
}

// --- 상태 WebSocket ---
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (ev) => render(JSON.parse(ev.data));
  ws.onclose = () => setTimeout(connectWS, 1000);
}

function badge(el, on, label) {
  el.textContent = label;
  el.className = "badge " + (on ? "on" : "off");
}

function render(s) {
  lastState = s;
  badge($("rpi-badge"), s.rpi_connected, "RPi");
  badge($("esp-badge"), s.esp32_seen, "ESP32");
  $("phase-badge").textContent = (s.phase || "idle").toUpperCase();

  $("s-phase").textContent = s.phase || "-";
  $("s-sel").textContent = s.selected_id != null ? `#${s.selected_id}` : "-";
  $("s-dist").textContent = s.distance_mm != null ? `${s.distance_mm} mm` : "-";
  $("s-ang").textContent = `${s.pan_current ?? "-"}° / ${s.tilt_current ?? "-"}°`;
  $("s-bias").textContent = `tilt ${s.tilt_bias ?? 0}° / pan ${s.pan_bias ?? 0}° / ${s.shots_count ?? 0}발`;
  $("s-msg").textContent = s.message || "-";

  // 결과 배너 + 피드백 패널 (결과 단계에서만)
  const res = $("result");
  const fb = $("feedback");
  if (s.result === "HIT" || s.result === "MISS") {
    res.textContent = s.result === "HIT" ? "🎯 HIT" : "❌ MISS";
    res.className = "result " + (s.result === "HIT" ? "hit" : "miss");
    fb.className = "feedback";       // 표시
  } else {
    res.className = "result hidden";
    fb.className = "feedback hidden"; // 숨김
  }

  // 검출 목록
  const list = $("det-list");
  if (!s.detections || s.detections.length === 0) {
    list.innerHTML = '<li class="empty">검출 없음</li>';
  } else {
    list.innerHTML = "";
    for (const d of s.detections) {
      const li = document.createElement("li");
      if (d.id === s.selected_id) li.className = "selected";
      li.innerHTML = `<span>#${d.id} ${d.label}</span>` +
                     `<span class="conf">${(d.conf * 100).toFixed(0)}%</span>`;
      li.addEventListener("click", () => post(`/api/select/${d.id}`));
      list.appendChild(li);
    }
  }
}

connectWS();
