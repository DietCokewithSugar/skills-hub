import type { Metadata } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans, Noto_Sans_SC } from "next/font/google";
import { MotionProvider } from "@/lib/motion";
import "@/styles/globals.css";

/* 7.3：中文 Noto Sans SC，拉丁与数字 IBM Plex Sans，
   代码/日志/计时器/token 数 IBM Plex Mono。
   IBM Plex 的工程制图出身与「工位」的主题一致。 */
const plexSans = IBM_Plex_Sans({
  subsets: ["latin"], weight: ["400", "500"],
  variable: "--font-plex-sans", display: "swap",
});
const plexMono = IBM_Plex_Mono({
  subsets: ["latin"], weight: ["400", "500"],
  variable: "--font-plex-mono", display: "swap",
});
const notoSC = Noto_Sans_SC({
  subsets: ["latin"], weight: ["400", "500"],
  variable: "--font-noto-sc", display: "swap",
});

export const metadata: Metadata = {
  title: "工位",
  description: "在线 Skill 执行平台",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN"
          className={`${plexSans.variable} ${plexMono.variable} ${notoSC.variable}`}>
      <body>
        <MotionProvider>{children}</MotionProvider>
      </body>
    </html>
  );
}
