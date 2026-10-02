"use client";
import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { ApiError, authPost } from "../../lib/api";
export default function Login() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const form = new FormData(event.currentTarget);
    try {
      await authPost(
        "/auth/login",
        new URLSearchParams({
          username: String(form.get("username")),
          password: String(form.get("password")),
        }),
      );
      router.replace("/");
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 401
          ? "아이디 또는 비밀번호를 확인하세요."
          : e instanceof ApiError && e.status === 429
            ? "로그인 시도가 많습니다. 잠시 후 다시 시도하세요."
            : "로그인하지 못했습니다. 연결을 확인하고 다시 시도하세요.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="login-shell">
      <section className="login-story">
        <a className="brand" href="/">
          FIELD<span>CONTROL</span>
        </a>
        <div className="story-content">
          <p className="eyebrow">CONNECTED FIELD OPERATIONS</p>
          <h1>
            현장의 신호를,
            <br />
            한눈에.
          </h1>
          <p>
            장치의 연결부터 침입 이벤트까지.
            <br />
            현장 감시의 흐름을 중앙에서 확인하세요.
          </p>
          <div className="signal-art" aria-hidden="true">
            <div className="orbit one" />
            <div className="orbit two" />
            <div className="orbit three" />
            <span className="signal-center">F</span>
            <span className="signal-dot dot-a" />
            <span className="signal-dot dot-b" />
            <span className="signal-dot dot-c" />
          </div>
        </div>
        <span className="story-foot">FIELD CONTROL · CENTRAL CONSOLE</span>
      </section>
      <section className="login-form-wrap">
        <form className="login-form" onSubmit={submit}>
          <p className="eyebrow">ADMIN ACCESS</p>
          <h2>관리자 로그인</h2>
          <p className="muted">관제 화면에 접속하려면 로그인하세요.</p>
          <label htmlFor="username">아이디</label>
          <input
            id="username"
            name="username"
            autoComplete="username"
            required
            maxLength={64}
            placeholder="관리자 아이디"
          />
          <label htmlFor="password">비밀번호</label>
          <input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            placeholder="비밀번호 입력"
          />
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <button className="primary login-button" disabled={busy}>
            {busy ? "로그인 중…" : "로그인"}
            <span aria-hidden="true">→</span>
          </button>
          <p className="login-note">
            승인된 관리자 계정으로만 접속할 수 있습니다.
          </p>
        </form>
      </section>
    </main>
  );
}
