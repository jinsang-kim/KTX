const $ = (s) => document.querySelector(s);

// APP_TOKEN 을 설정했다면 최초 1회 입력받아 저장
let appToken = localStorage.getItem("appToken") || "";

async function api(path, opts = {}) {
  const res = await fetch(`/api${path}`, {
    ...opts,
    headers: { "Content-Type": "application/json", "X-App-Token": appToken, ...(opts.headers || {}) },
  });
  if (res.status === 401) {
    appToken = prompt("앱 접근 토큰을 입력하세요") || "";
    localStorage.setItem("appToken", appToken);
    return api(path, opts);
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail));
  return body;
}

const fmtTime = (t) => `${t.slice(0, 2)}:${t.slice(2, 4)}`;
const fmtDate = (d) => `${d.slice(4, 6)}/${d.slice(6, 8)}`;

// ---------- 초기화 ----------
let lastSearch = null;
let selected = []; // 체크한 순서 = 우선순위
let lastTrains = {}; // train_no → TrainInfo

(async function init() {
  $("#search-form").date.valueAsDate = new Date();
  const [health, stations] = await Promise.all([api("/health"), api("/stations")]);
  $("#provider-badge").textContent = health.provider === "mock" ? "MOCK 모드 (가짜 데이터)" : "코레일 연동";
  $("#stations").innerHTML = stations.map((s) => `<option value="${s}">`).join("");
  if ("Notification" in window && Notification.permission === "default") Notification.requestPermission();
  refreshWatches();
  setInterval(refreshWatches, 3000);
  refreshNotify();
  if (new URLSearchParams(location.search).get("kakao") === "connected") {
    notifyMsg("카카오톡이 연결되었습니다. '테스트 알림 보내기'로 확인해 보세요.", true);
    history.replaceState(null, "", "/");
  }
})();

// ---------- 알림 설정 ----------
const CHANNEL_LABEL = { kakao: "카카오톡", email: "Gmail", telegram: "텔레그램" };

function notifyMsg(text, ok = false) {
  $("#notify-msg").textContent = text;
  $("#notify-msg").classList.toggle("ok", ok);
}

async function refreshNotify() {
  const { channels, kakao_configured } = await api("/notify/status");
  $("#notify-status").innerHTML = Object.entries(channels)
    .map(([k, on]) => `<span class="chip ${on ? "on" : ""}">${CHANNEL_LABEL[k]} ${on ? "켜짐" : "꺼짐"}</span>`)
    .join("");
  // 카카오는 REST API 키가 설정된 경우에만 연결 버튼 노출
  $("#kakao-connect").hidden = !kakao_configured || channels.kakao;
  $("#kakao-connect").href = `/api/kakao/login?token=${encodeURIComponent(appToken)}`;
  $("#kakao-disconnect").hidden = !channels.kakao;
}

$("#notify-test").addEventListener("click", async () => {
  notifyMsg("보내는 중...");
  try {
    const results = await api("/notify/test", { method: "POST" });
    const failed = Object.entries(results).filter(([, r]) => r !== "ok");
    notifyMsg(
      failed.length
        ? failed.map(([k, r]) => `${CHANNEL_LABEL[k]}: ${r}`).join(" / ")
        : `전송 완료: ${Object.keys(results).map((k) => CHANNEL_LABEL[k]).join(", ")}`,
      !failed.length
    );
  } catch (err) {
    notifyMsg(err.message);
  }
});

$("#kakao-disconnect").addEventListener("click", async () => {
  await api("/kakao", { method: "DELETE" });
  notifyMsg("카카오톡 연결을 해제했습니다.", true);
  refreshNotify();
});

// ---------- 조회 ----------
$("#search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const req = {
    dep: f.dep.value.trim(),
    arr: f.arr.value.trim(),
    date: f.date.value.replaceAll("-", ""),
    time: f.time.value.replace(":", "") + "00",
  };
  $("#search-msg").textContent = "조회 중...";
  try {
    const trains = await api(`/search?adults=${f.adults.value}`, { method: "POST", body: JSON.stringify(req) });
    lastSearch = { ...req, adults: Number(f.adults.value) };
    selected = [];
    renderTrains(trains);
    $("#search-msg").textContent = trains.length ? "" : "조회된 열차가 없습니다.";
  } catch (err) {
    $("#search-msg").textContent = err.message;
  }
});

function renderTrains(trains) {
  lastTrains = Object.fromEntries(trains.map((t) => [t.train_no, t]));
  const seat = (ok) => (ok ? `<span class="yes">예약가능</span>` : `<span class="no">매진</span>`);
  $("#train-table tbody").innerHTML = trains
    .map(
      (t) => `<tr>
        <td><input type="checkbox" data-no="${t.train_no}" /><span class="prio"></span></td>
        <td>${t.train_type_name} ${t.train_no}</td>
        <td>${fmtTime(t.dep_time)}</td>
        <td>${fmtTime(t.arr_time)}</td>
        <td>${seat(t.general_available)}</td>
        <td>${seat(t.special_available)}</td>
      </tr>`
    )
    .join("");
  $("#train-table").hidden = false;
  updateSelection();
}

$("#train-table").addEventListener("change", (e) => {
  const no = e.target.dataset.no;
  if (!no) return;
  selected = e.target.checked ? [...selected, no] : selected.filter((n) => n !== no);
  updateSelection();
});

function updateSelection() {
  document.querySelectorAll("#train-table input[data-no]").forEach((cb) => {
    const idx = selected.indexOf(cb.dataset.no);
    cb.nextElementSibling.textContent = idx >= 0 ? idx + 1 : "";
  });
  $("#start-watch").disabled = selected.length === 0;
}

// ---------- 감시 시작 ----------
$("#start-watch").addEventListener("click", async () => {
  $("#watch-msg").textContent = "";
  try {
    await api("/watches", {
      method: "POST",
      body: JSON.stringify({
        dep: lastSearch.dep,
        arr: lastSearch.arr,
        date: lastSearch.date,
        train_nos: selected,
        search_time: selected.map((n) => lastTrains[n].dep_time).sort()[0],
        seat_option: $("#seat-option").value,
        passengers: { adults: lastSearch.adults },
      }),
    });
    refreshWatches();
  } catch (err) {
    $("#watch-msg").textContent = err.message;
  }
});

// ---------- 감시 현황 ----------
const STATUS_LABEL = {
  watching: "감시 중",
  reserved: "예약 완료",
  failed: "실패",
  cancelled: "취소됨",
  expired: "만료",
};
const notified = new Set(JSON.parse(localStorage.getItem("notified") || "[]"));

async function refreshWatches() {
  let watches;
  try {
    watches = await api("/watches");
  } catch {
    return;
  }
  for (const w of watches) {
    if (w.status === "reserved" && !notified.has(w.id)) {
      notified.add(w.id);
      localStorage.setItem("notified", JSON.stringify([...notified]));
      if ("Notification" in window && Notification.permission === "granted") {
        new Notification("🚄 KTX 예약 완료!", { body: `${w.reservation.train_no} 열차 · 결제 기한 ${w.reservation.payment_deadline}` });
      }
    }
  }
  $("#watch-list").innerHTML = watches.length
    ? watches.map(renderWatch).join("")
    : `<p class="hint">감시 중인 작업이 없습니다.</p>`;
}

function renderWatch(w) {
  const r = w.reservation;
  return `<div class="watch">
    <div class="watch-head">
      <strong>${w.dep} → ${w.arr} · ${fmtDate(w.date)} · 열차 ${w.train_nos.join(", ")}</strong>
      <span class="status ${w.status}">${STATUS_LABEL[w.status]}</span>
    </div>
    <div class="meta">
      조회 ${w.attempts}회 ${w.last_checked_at ? "· 마지막 " + new Date(w.last_checked_at).toLocaleTimeString() : ""}
      ${w.last_error ? `· <span style="color:var(--err)">${w.last_error}</span>` : ""}
    </div>
    ${
      r
        ? `<div class="reserved-box">
            <strong>예약번호 ${r.rsv_id}</strong> · ${r.train_no} ${fmtTime(r.dep_time)} 출발 · ${r.seat_class}
            ${r.price ? `· ${r.price.toLocaleString()}원` : ""}<br/>
            ⏰ <b>${r.payment_deadline ?? "-"}</b> 까지 코레일톡/홈페이지에서 결제해야 확정됩니다.
          </div>`
        : ""
    }
    ${w.status === "watching" ? `<div><button class="ghost" onclick="cancelWatch('${w.id}')">감시 중지</button></div>` : ""}
  </div>`;
}

async function cancelWatch(id) {
  await api(`/watches/${id}`, { method: "DELETE" });
  refreshWatches();
}
