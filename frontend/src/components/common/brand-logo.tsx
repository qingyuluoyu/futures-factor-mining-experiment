import { BRAND_NAME } from "@/lib/brand"

interface BrandLogoProps {
  size?: number
  className?: string
  decorative?: boolean
}

export function BrandLogo({ size = 32, className = "", decorative = false }: BrandLogoProps): React.JSX.Element {
  return (
    <img
      src="/wenyuan-mark.svg"
      alt={decorative ? "" : BRAND_NAME}
      width={size}
      height={size}
      draggable={false}
      className={`shrink-0 ${className}`}
    />
  )
}
