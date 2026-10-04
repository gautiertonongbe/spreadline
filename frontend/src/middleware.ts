import { NextResponse, type NextRequest } from "next/server";

/**
 * Turn away a request with no session before it renders anything.
 *
 * This is a cheap first pass, not the security boundary. All it can see is
 * whether a cookie is present; whether that cookie is any good is a question
 * only the API can answer, and the app layout asks it on every render. The two
 * together mean a signed-out visitor never waits for a round trip to be told to
 * sign in, and a stale cookie never renders a page.
 *
 * It works because the web app and the API are same-site, which cookie
 * authentication with server-rendered pages requires anyway: a cookie set on
 * another site is one this middleware and the server renderer both cannot see.
 */
const SESSION_COOKIE = "spreadline_session";

/** Paths that render without a session. Everything else needs one. */
const PUBLIC_PATHS = ["/login"];

export function middleware(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const signedIn = Boolean(request.cookies.get(SESSION_COOKIE)?.value);
  const isPublic = PUBLIC_PATHS.some(
    (path) => pathname === path || pathname.startsWith(`${path}/`),
  );

  if (isPublic) {
    // Somebody already signed in has no business on the sign-in page.
    return signedIn
      ? NextResponse.redirect(new URL("/", request.url))
      : NextResponse.next();
  }

  if (signedIn) return NextResponse.next();

  const destination = new URL("/login", request.url);
  // Carried so signing in lands where the reader was going, and only ever a
  // path on this site: an open redirect is a phishing tool with a login form
  // attached.
  if (pathname !== "/") destination.searchParams.set("next", `${pathname}${search}`);
  return NextResponse.redirect(destination);
}

export const config = {
  // Everything but Next's own assets and the favicon, which render before any
  // session exists and would otherwise redirect the page's own stylesheet.
  matcher: ["/((?!_next/static|_next/image|favicon.ico|icon.svg).*)"],
};
