import type { Metadata } from "next"
import "./globals.css"
import { FontSizeProvider } from "@/components/common/font-size-provider"
import { BRAND_NAME, BRAND_DESCRIPTION } from "@/lib/brand"

export const metadata: Metadata = {
  title: BRAND_NAME,
  applicationName: BRAND_NAME,
  description: BRAND_DESCRIPTION,
  icons: {
    icon: "/wenyuan-mark.svg",
    shortcut: "/wenyuan-mark.svg",
    apple: "/wenyuan-apple.png",
  },
}

/** 首屏前恢复字号，避免闪一下再变大 */
const FONT_SIZE_BOOT_SCRIPT = `(function(){try{var k='qihuo_font_size';var s=localStorage.getItem(k)||'standard';var ok={standard:1,large:1,xlarge:1,xxlarge:1};if(!ok[s])s='standard';document.documentElement.setAttribute('data-font-size',s);}catch(e){document.documentElement.setAttribute('data-font-size','standard');}})();`

/** 首屏前恢复主题（默认浅色），避免深色闪一下再变白 */
const THEME_BOOT_SCRIPT = `(function(){try{var t=localStorage.getItem('qihuo_theme');var dark=(t==='dark');document.documentElement.classList.toggle('dark',dark);document.documentElement.setAttribute('data-theme',dark?'dark':'light');}catch(e){document.documentElement.setAttribute('data-theme','light');}})();`

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="zh-CN" data-font-size="standard" data-theme="light">
      <head>
        <script dangerouslySetInnerHTML={{ __html: FONT_SIZE_BOOT_SCRIPT }} />
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT_SCRIPT }} />
      </head>
      <body className="antialiased font-sans">
        <FontSizeProvider />
        {children}
      </body>
    </html>
  )
}
