import type { ReactNode } from "react";
import { COPY } from "../copy.ts";
import { dateOnly } from "../format.ts";
import { B, STEP } from "../timing.ts";
import { colour, layout, size, spacing } from "../theme.ts";
import { Abs, Appear, Body, Col, Head, Mono, Page, SealRule, Typed, useFilm } from "../ui/kit.tsx";

/** Scene 9: what the film does not show, in the README's own sentences, with nothing fading out, and where its numbers came from. */
export function Scene9(): ReactNode {
  const film = useFilm();
  const b = B.s9;
  const rendered = film.dev ? film.meta.rendered : dateOnly(film.meta.rendered);
  return (
    <Page>
      <Col gap={spacing.m}>
        <Appear at={b.title}>
          <Head fontSize={size.head}>{COPY.s9.title}</Head>
        </Appear>
        <SealRule at={b.title} width={layout.ruleWide} />
        <Col gap={spacing.m} style={{ paddingTop: spacing.m }}>
          {COPY.s9.lines.map((line, i) => (
            <Appear key={line} at={b.lines + i * STEP.limitGap}>
              <Body fontSize={size.lead}>{line}</Body>
            </Appear>
          ))}
        </Col>
      </Col>
      <Abs left={layout.origin} bottom={layout.origin} width={layout.page}>
        <Col gap={spacing.s}>
          <Mono small style={{ color: colour.graphite, fontSize: size.tiny }}>
            <Typed text={COPY.s9.footerHead} at={b.footer} style={{ fontFamily: "inherit" }} />
          </Mono>
          <Mono small style={{ color: colour.graphite, fontSize: size.tiny }}>
            <Typed text={`${COPY.s9.commit} ${film.meta.commit}. ${COPY.s9.rendered} ${rendered}.`} at={b.footer + STEP.footerGap} style={{ fontFamily: "inherit" }} />
          </Mono>
          <Mono small style={{ color: colour.graphite, fontSize: size.tiny }}>
            <Typed text={`${COPY.s9.record} ${film.meta.recordSha256}`} at={b.footer + STEP.footerGap + STEP.footerGap} style={{ fontFamily: "inherit" }} />
          </Mono>
        </Col>
      </Abs>
    </Page>
  );
}
