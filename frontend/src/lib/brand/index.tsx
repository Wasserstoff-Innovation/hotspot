import type { LogoProps, LogomarkProps } from '@posthog/brand/logo'
import { cn } from 'lib/utils/css-classes'

/**
 * Named logo sizes. Callers pick one of these instead of hand-tuning pixel dimensions per surface —
 * `md` is the standard logo. A token sets the rendered **height**; width always follows from aspect ratio.
 */
export type LogoSize = 'xs' | 'sm' | 'md' | 'lg' | 'xl'

const LOGO_SIZE_HEIGHTS: Record<LogoSize, number> = {
    xs: 20, // minimal: exporter chrome
    sm: 24, // compact: watermarks, inline marks
    md: 28, // default: standard logo (nav, player, most surfaces)
    lg: 32, // prominent: page headers (login, onboarding)
    xl: 48, // hero: emphasis surfaces
}

type WithLogoSize<P> = Omit<P, 'size' | 'width' | 'height'> & { size?: LogoSize }
export type AppLogoProps = WithLogoSize<LogoProps>
export type AppLogomarkProps = WithLogoSize<LogomarkProps>

export function HotspotMark({
    size = 28,
    className,
    style,
}: {
    size?: number
    className?: string
    style?: React.CSSProperties
}): JSX.Element {
    return (
        <svg
            viewBox="0 0 32 32"
            width={size}
            height={size}
            className={className}
            style={{ display: 'inline-block', verticalAlign: 'middle', flexShrink: 0, ...style }}
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
        >
            <defs>
                <linearGradient id="hotspot-beacon-grad" x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stopColor="#FF6B00" />
                    <stop offset="100%" stopColor="#F54E00" />
                </linearGradient>
                <linearGradient id="hotspot-core-grad" x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stopColor="#FFA000" />
                    <stop offset="100%" stopColor="#FF3D00" />
                </linearGradient>
            </defs>
            <circle
                cx="16"
                cy="16"
                r="14"
                stroke="url(#hotspot-beacon-grad)"
                strokeWidth="2"
                strokeDasharray="3 3"
                opacity="0.45"
            />
            <circle cx="16" cy="16" r="9.5" stroke="url(#hotspot-beacon-grad)" strokeWidth="2.5" opacity="0.85" />
            <circle cx="16" cy="16" r="5.2" fill="url(#hotspot-core-grad)" />
            <circle cx="14.8" cy="14.8" r="1.6" fill="#FFF" opacity="0.9" />
        </svg>
    )
}

export function HotspotLogoSvg({
    height = 28,
    className,
    isDark = false,
    style,
}: {
    height?: number
    className?: string
    isDark?: boolean
    style?: React.CSSProperties
}): JSX.Element {
    const width = Math.round(height * (140 / 28))
    const textColor = isDark ? '#FFFFFF' : '#111827'
    return (
        <svg
            viewBox="0 0 140 28"
            width={width}
            height={height}
            className={className}
            style={{ display: 'inline-block', verticalAlign: 'middle', flexShrink: 0, ...style }}
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
        >
            <defs>
                <linearGradient id="hotspot-logo-grad" x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stopColor="#FF6B00" />
                    <stop offset="100%" stopColor="#F54E00" />
                </linearGradient>
                <linearGradient id="hotspot-logo-core" x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stopColor="#FFA000" />
                    <stop offset="100%" stopColor="#FF3D00" />
                </linearGradient>
            </defs>
            <g transform="translate(0, 0)">
                <circle
                    cx="14"
                    cy="14"
                    r="12"
                    stroke="url(#hotspot-logo-grad)"
                    strokeWidth="1.8"
                    strokeDasharray="2.5 2.5"
                    opacity="0.45"
                />
                <circle cx="14" cy="14" r="8" stroke="url(#hotspot-logo-grad)" strokeWidth="2.2" opacity="0.85" />
                <circle cx="14" cy="14" r="4.5" fill="url(#hotspot-logo-core)" />
                <circle cx="13" cy="13" r="1.4" fill="#FFF" opacity="0.9" />
            </g>
            <text
                x="33"
                y="19.5"
                fill={textColor}
                fontFamily="Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
                fontWeight="800"
                fontSize="18"
                letterSpacing="-0.5px"
            >
                Hotspot
            </text>
        </svg>
    )
}

/**
 * The Hotspot brand logo, adapted to the app's theme.
 */
export function Logo({ variant, className, size, ...props }: AppLogoProps): JSX.Element {
    const height = size ? LOGO_SIZE_HEIGHTS[size] : 28
    if (variant === 'mono') {
        return <HotspotLogoSvg height={height} className={className} isDark={(props as any).color === 'white'} />
    }
    return (
        <>
            <HotspotLogoSvg height={height} className={cn('dark:hidden', className)} isDark={false} />
            <HotspotLogoSvg height={height} className={cn('hidden dark:block', className)} isDark={true} />
        </>
    )
}

/**
 * The Hotspot beacon logomark.
 */
export function Logomark({ className, size }: AppLogomarkProps): JSX.Element {
    const height = size ? LOGO_SIZE_HEIGHTS[size] : 28
    return <HotspotMark size={height} className={className} />
}

Logo.Logomark = Logomark
