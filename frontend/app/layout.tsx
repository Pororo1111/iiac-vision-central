import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {
  title: "FIELD / 중앙 관제",
  description: "현장 장치와 침입 이벤트를 한 곳에서 확인합니다.",
};
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
