# Axiom: færre tokens, kortere kjøretid og bedre leveranser

Undersøkt 2026-09-16. Grunnlag: produktrepoet ved `04f885c`, lokale oppgaver lest
fra SQLite uten endringer, nåværende runtime og primærkilder på GitHub.
Ingen nye betalte modellkjøringer, installasjoner eller arkitekturendringer er utført.

## Anbefaling

Behold LangGraph. Prioriter et tokenregnskap, målrettede filverktøy, korte
strukturerte overleveringer og gjenbruk av deterministisk verifisering.
Legg deretter til en isolert runner for faktisk kjøring og nettlesersjekker.
Sammenlign dagens fire roller med en enklere flyt i kontrollerte forsøk.

Langfuse er en god kandidat for observability og evaluering. Det reduserer ikke
agentarbeidet av seg selv. Graphifyy kan hjelpe med navigasjon i eksisterende kode,
men løser ikke hovedkostnaden ved å generere et spill fra et tomt prosjekt.
Aider, Serena, Headroom og Deep Agents har mer direkte relevante teknikker å lære av.

Oppdatert etter at brukeren sendte den konkrete spilldemoen: kilden oppgir svært
høy cacheandel, ikke lavt totalt tokenvolum. Prisregnestykket i Grok-svaret er feil
for Astra ved standard API-priser. Se seksjon 4 for kildeverifisering og beregninger.

Målet bør være lavest kostnad og tid per **verifisert god leveranse**, ikke færrest
tokens i hver enkelt agent. En dyrere Coder kan spare flere reparasjonsrunder.

## 1. Hva vi faktisk har målt

Historiske oppgaver inneholder ikke provider-usage, kostnad eller komplette
request/response-spor. Det er derfor ikke mulig å rekonstruere eksakte historiske
tokenregnskap fra denne databasen. Tegn og filstørrelser nedenfor er ikke tokens.

### Varg, oppgave `744af3f3`

Dette var en **videreføring med eksisterende kode**, ikke en ren fra-null-test.
Den ble fullført med approved, 38 modellkall og ingen subagenter.

| Rolle | read_file | validate_file | Sluttrapport, tegn |
|---|---:|---:|---:|
| Planner | 8 | 7 | 3 802 |
| Coder | 3 | 10 | 2 094 |
| Tester | 8 | 8 | 4 159 |
| Reviewer | 10 | 8 | 2 063 |
| Sum | 29 | 33 | 12 118 |

I tillegg inneholder den automatiske verifiseringen 8 sjekker. Samlet er det
41 eksplisitte/automatiske filkontroller, men noen kan være legitime kontroller
av forskjellige filversjoner. Vi har ikke hashes fra hver historiske kontroll.

Workspace har 10 filer, totalt 59 356 bytes. `src/render3d.js` er 22 443 bytes og
ble lest fire ganger. `index.html` og `src/world.js` ble også lest fire ganger.
Verktøyhistorikken inneholder 90 hendelser, under grensen på 300 lagrede hendelser.

Valgte rollemodeller i oppgaven: Planner `anthropic/claude-fable-5`, Coder
`z-ai/glm-5.3-flash`, Tester `google/gemini-3.8-flash`, Reviewer
`openai/gpt-5.6-sol`. Toppfeltet `model` er `openrouter/free`, men rolleoverstyringene
viser hvorfor dette feltet alene ikke er et gyldig grunnlag for modellkostnad.
Dette er ikke dokumentasjon på Astra-versus-DeepSeek-effektivitet.

### Andre oppgaver

- `4e5f4e80`: 39 modellkall, failed, ingen workers. Planner-rapport 9 085 tegn,
  Tester-rapport 5 394 tegn. Repair ble aktivert før kjøringen stoppet.
- `f4dbba1f`: eldre runtime, failed; 24 write_file-kall og 17 validate_file-kall
  hos Coder. Sluttworkspace har 13 filer. Dette viser gjentatte skrivinger, men
  uten de gamle argumentene vet vi ikke hvor mye kode som ble skrevet om.

Nåværende lokale konfigurasjon har 32 768 som outputtak og 2 048 som forespurt
reasoning-budget. Et tak betyr ikke at hver request bruker dette antallet.
Providerstøtte avgjør hvordan reasoning-parameteren blir tolket.

## 2. De viktigste kostnadsdriverne i vår kode

### A. Vi kaster målingene som allerede kommer fra OpenRouter

`backend/provider.py`, `OpenRouter.complete`, beholder bare assistant-message og
finish_reason. Usage, generation-ID og den returnerte modellidentiteten lagres ikke.
OpenRouter dokumenterer at usage automatisk følger svaret, også cached/reasoning
tokens der de er tilgjengelige [S1]. Ingen ekstra inferens er nødvendig for dette.

Foreslått record per faktisk provider-forsøk:

```
task_id, role, worker_id, repair_round, logical_call_id, attempt,
requested_model, resolved_model, provider_if_available, generation_id,
prompt_tokens, completion_tokens, reasoning_tokens,
cached_tokens, cache_write_tokens, provider_cost,
latency_ms, finish_reason, error_type, prompt_version, harness_version
```

Behold manglende verdier som null. Reasoning er normalt en del av completion;
cached er en del av input. Ikke legg underkategoriene til totalsummen en gang til.
Mål også verktøytid, resultatstørrelse og ventetid. Ikke send selve dette regnskapet
til modellen i hver runde. Skill observability fra agentkontekst.

### B. read_file returnerer hele filen

`backend/workspace.py` tilbyr helfilinnlesing opp til 128 000 bytes, literal search
og filliste. Det finnes ingen linjeintervall-, symbol- eller diff-innlesing.
Å hente én funksjon i renderer kan derfor kreve hele filen og senere replay av den.

Start med billige deterministiske verktøy:

- `read_file(path, start_line, end_line)` med linjenummer og eksplisitt avkorting.
- `file_outline(path)` med imports, funksjoner/klasser og signaturer.
- `read_symbol(path, symbol)` og `find_references` der parser/LSP støtter det.
- `changed_files` og `read_diff` fra oppgavens eller repair-rundens start.
- Et lite relevansrangert repo-kart; full kilde hentes bare ved behov.

Helfilinnlesing må fortsatt finnes. Et kart er en pekepinn, ikke bevis for korrekt kode.
Verktøyene må beholde Workspace-grensen og rollenes skriverettigheter.

### C. Historikken vokser og sendes på nytt

Hver runde sender messages på nytt. `compact_messages` bruker 200 000 tegn fra
JSON-serialisering som terskel, ikke modellens tokenizer. Første to og siste seks
meldinger skjermes; terskelen er derfor ikke en garantert hard grense.
Gamle outputs erstattes med «read again», uten et strukturert arbeidsminne.

Illustrasjon, ikke målt Axiom-resultat: Startkontekst B og g nye tokens per runde
gir omtrent `n*B + g*n*(n-1)/2` kumulative inputtokens når alt blir sendt videre.
Med B=2 000, g=2 000 og n=20 blir det 420 000 inputtokens, selv om siste input er
40 000. Cached input kan gjøre replay billigere, men endrer ikke logisk kontekststørrelse.

Bedre løsning: korte tool-resultater fra starten, originaler utenfor prompten,
referanser med filhash/versjon, og et kompakt arbeidsnotat ved faseoverganger.
Ikke gjør terskelen mye mindre alene; det kan øke antall gjeninnlesinger.
Ikke endre et allerede cachet prefiks hver runde bare for å spare noen få tegn.

### D. Hele rapporter går videre til nye rapportlesere

`run_agent` legger tidligere rapporter i første user-message. Coder får alle
tidligere roller ved repair. Tester og Reviewer får sine oppstrømsrapporter.
`repair_feedback` kopierer dessuten Tester/Reviewer og verification, og kan
duplisere innhold i `prior_results`. Continuation bærer gamle rapporter videre.
Rapporter har opptil 24 000 tegn per rolle; avkortingen skjer etter generering,
så den sparer ikke tokens som modellen allerede har produsert.

Bruk en validert, strukturert overlevering: krav-ID, beslutning, berørte filer,
offentlige grensesnitt, konkrete funn og uløste spørsmål. Lagre den lange rapporten
for brukeren, men send ikke hele teksten videre automatisk. Reviewer kan starte
fra krav, diff og uavhengige måleresultater før den ser de andre modellenes vurdering.

### E. Flere agenter gjentar samme syntakskontroll

Grafen kjører allerede automatisk validate etter Coder, men Tester instrueres til
å kalle validate_file for hver støttet fil. Historiske Planner/Reviewer gjør det også.
Tre modeller som får samme parser til å lese samme bytes gir ikke tre uavhengige tester.

Lag en deterministisk verifiseringsrecord nøstet under filhash, validatorversjon
og relevant konfigurasjon. Gjenbruk kun for uendrede forutsetninger. Valider hele
prosjektets nødvendige invarianter, men be ikke modellen gjenta cachede parsingkall.
La Tester bruke tiden på atferd og konkrete risikoer.

### F. Feil blir oppdaget sent fordi vi ikke kjører produktet

Pipelinen parser kilde, men starter aldri spillet. Den finner ikke sikkert feil i
importer, assets, kamera, input, kollisjoner, restart eller rendering.
Dette svekker kvalitet og kan utløse dyre menneskedrevne videreføringer senere.

Foreslå en separat runner med deklarative operasjoner: build, avgrensede tester og
browser-smoke. Generert kode må kjøre i en faktisk isolert miljøgrense uten nøkler
eller vertsfilsystem, med tids-/ressursgrenser. En Python-prosess alene er ikke en
sandbox. Dette er en bevisst utvidelse av ADR-007/008, ikke bare en promptendring.
Returner exit code, korte feil, artifact-ID, hash og et skjermbilde der det hjelper.
Lagre full logg separat. Spillfølelse trenger fortsatt menneskelig eller målrettet
visuell vurdering; én grønn nettleserstart beviser ikke at spillet er bra.

### G. En fast fire-agent-runde er ikke alltid den billigste flyten

LangGraph gir tydelig kontrollflyt og avgrensede reparasjoner. Den reduserer ikke
automatisk antall modellkall eller størrelsen på meldingene. Dagens graf kjører
alle fire roller sekvensielt også for små endringer.

Test en alternativ konfigurasjon, uten å erstatte dagens kontrakt stille:

```
Krav + selektiv plan -> Coder -> deterministiske checks/runner
                                    -> selektiv review -> ferdig
                          konkret feil -> målrettet repair
```

For komplekse spill er en kort arkitekturplan nyttig. For én liten rettelse kan en
egen lang Planner- og Tester-samtale være dyrere enn arbeidet. Behold uavhengig
review for større endringer og risiko, og behold fail-closed deterministisk validering.
Subagenter er primært et latency-tiltak når arbeidet er uavhengig. De kan øke totale
tokens gjennom kontekstkopier, rapporter og integrasjon. Ikke krev workers for å spare.

### H. Reasoning og modellvalg trenger måling per rolle

Alle roller får samme globale outputtak og reasoning-innstilling. Én HTTP 400 med
reasoning gjør dessuten `reasoning_supported=False` på den delte provider-instansen.
Dette er et sted å undersøke per-modell capability og feilklassifisering, ikke et
bevis på at 2 048 faktisk håndheves for alle modeller.

Test lavere reasoning for rutinearbeid, høyere for arkitektur og vanskelige feil.
Ikke slå av reasoning globalt, og ikke sett et lavt outputtak som kutter kodefiler.
Modell-ID og provider må være stabile under sammenligninger; `openrouter/free`
og bevegelige latest-aliaser er dårlige kontrollbetingelser.

## 3. GitHub-prosjekter: hva vi bør bruke og hvorfor

Dette er kildebasert vurdering, ikke en lokal ytelsestest av pakkene. Oppgitte
benchmarkresultater er prosjektenes egne, og kan ikke overføres direkte til Axiom.

| Prosjekt | Relevant mekanisme | Vurdering for Axiom |
|---|---|---|
| [Langfuse](https://github.com/langfuse/langfuse) [S4] | Traces, usage/kostnad, promptversjoner, datasets, evaluering | Anbefalt målingslag. Behold også et lokalt provider-ledger. Ingen automatisk tokenbesparelse. |
| [Aider](https://github.com/Aider-AI/aider) [S5] | Relevansrangert repo-kart med symbolsignaturer og tokenbudsjett | Høy relevans; adopter teknikken uten å bytte ut hele runtime. Dokumentert map-default er ca. 1k tokens, dynamisk justert. Apache-2.0. |
| [Serena](https://github.com/oraios/serena) [S6] | Symbolinnlesing, references, symbolredigering via LSP | Høy relevans ved vedlikehold av eksisterende kode. MCP krever adapter i Axiom; velg et smalt verktøysett. App GPL-3.0-or-later, SolidLSP MIT. |
| [Graphify / graphifyy](https://github.com/Graphify-Labs/graphify) [S7] | Lokal AST-graf, imports/calls og målrettede grafspørringer | Test ved store eksisterende prosjekter. Mindre nytte ved tomt spillrepo. Apache-2.0. |
| [Headroom](https://github.com/headroomlabs-ai/headroom) [S8] | Python-bibliotek/proxy for kompresjon, originaler tilgjengelig via retrieval, cachebevisst behandling | Interessant isolert A/B-forsøk på store tool-outputs. Krever at retrieval faktisk er tilgjengelig og Workspace-avgrenset. Apache-2.0. |
| [Deep Agents](https://github.com/langchain-ai/deepagents) [S9] | LangGraph-basert harness med oppsummering, output til disk og isolert subagentkontekst | Nær dagens stack. Studer/adopter avgrensede deler før eventuell migrasjon; standardrettigheter må ikke overta våre rollegrenser. MIT. |
| [RTK](https://github.com/rtk-ai/rtk) [S10] | Deterministisk filtrering av shell-, test- og git-output | Bra når vi har en runner; dagens filverktøy går ikke gjennom shell og får ingen automatisk gevinst. Apache-2.0. |
| [GEPA](https://github.com/gepa-ai/gepa) / [DSPy](https://github.com/stanfordnlp/dspy) [S11] | Evalueringsstyrt promptoptimalisering | Aktuelt etter at benchmark og telemetry finnes. Optimaliser prompts offline, ikke ved å gjøre ekstra metakall på hver oppgave. |
| [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) [S12] | Enkel agentløype med få abstraksjoner | God kontrollvariant mot fire-agent-systemet. SWE-bench-resultater beviser ikke spillkvalitet; kopier ikke shell-tilgang ukritisk. MIT. |
| [Context Mode](https://github.com/mksglu/context-mode) [S13] | Store resultater beholdes utenfor kontekst, FTS5/BM25, programmatisk analyse | God designinspirasjon. ELv2 er source-available, ikke vanlig OSI-open-source; ikke sidestill lisensen med MIT/Apache. |
| [DCP](https://github.com/Opencode-DCP/opencode-dynamic-context-pruning) [S14] | Selektiv oppsummering, deduplisering, bevaring av feil | Lær av mekanismen. AGPL; utviklingen har flyttet fokus mot Sleev. Ikke anbefalt som ny direkte Axiom-avhengighet. |
| [LLMLingua](https://github.com/microsoft/LLMLingua) [S15] | Modellbasert tokenkompresjon | Lavere prioritet. Kan hjelpe lange dokumenter; ekstra modellkostnad/latency og mulig tap av viktige detaljer må måles. Ikke komprimer eksakt patchtekst tapsfullt. |

### Langfuse konkret

Instrumenter task -> role/attempt -> generation/tool. Axiom bruker egen httpx-client,
så en LangGraph-callback alene er ikke dokumentasjon på at provider-usage blir med.
Sett generation-spans rundt vårt provider-kall og send faktiske usage/cost-felt.
SDK-kilden beskriver nå v4/OpenTelemetry; unngå eldre v2-eksempler [S4].

Selvhosting er mer enn et Python-bibliotek: nåværende compose bruker web/worker,
Postgres, ClickHouse, Redis og MinIO. Port 3000 må endres fordi Axiom bruker den.
MIT-kjernen har separat lisensierte enterprise-kataloger. For denne lokale appen er
lokal SQLite-telemetry første steg; Langfuse blir spesielt verdifullt for å sammenligne
traces, promptversjoner og benchmarkkjøringer over tid. Det kan være valgfri exporter.

### Graphifyy konkret

Ja, `graphifyy` med to y-er er det offisielle Python-pakkenavnet; CLI heter graphify.
Kodegraf bygges deterministisk med tree-sitter uten LLM-kall. Semantisk behandling
av dokumenter og media kan bruke modell og må regnes med.

Benchmarkfilen deres, oppdatert 2026-07-05, viser kode-QA på ERPNext med **n=6**:
70,8% -> 82,0% key-fact coverage, omtrent 140k tokens per query. Den nevnte ~20x
sammenligningen er mot å fylle prompten med hele repoet, ikke en dokumentert 20x
forbedring over et godt målrettet Axiom-harness. Minnebenchmarkene er andre oppgaver.
Test grafen mot et enkelt Aider-lignende kart + søk før vi innfører flere lag.

### Headroom, RTK og store sparepåstander

Headrooms README har reproduserbare offline payloadmålinger: 21% på kode-søk,
42% på codebase exploration, større gevinst på repeterende logs/JSON. Dette måler
kompresjon av utvalgte input, ikke verifisert sluttkvalitet eller total spillkostnad.
RTK presiserer selv at opptil 90% gjelder shell-output, ikke hele regningen, og at
tokentallene deres estimeres som bytes/4. Context Modes 98% har tilsvarende snever
payloadbetydning. Slike tall er interessante hypoteser, ikke Axiom-løfter.

## 4. Kan en ekstremt god prompt forklare et imponerende spill?

Ja, delvis. En presis prompt kan fjerne avklaringer, unngå scope drift og velge riktig
stack og ambisjonsnivå. Men den lager ikke gratis kildekode.

### Den konkrete demoen fra Alexey Fateev

Originalinnlegget [S16], lest direkte i nettleser, sier samme prompt og hvert spill
bygget fra bunnen av, **Astra medium reasoning og DeepSeek max reasoning** gjennom
hele kjøringen. Forfatteren foretrekker subjektivt DS-lead/Astra-implementer.
Det offentlige spillrepoet [S17] bekrefter dette i fire game.json-filer og merker
spillene Three.js/WebGL. Repoet er en portal med ferdige statiske spillbygg.
Den eksakte originalprompten, fullstendige kjøringsspor og harnessinnstillinger er
ikke verifisert i materialet som er undersøkt. Ikke tilskriv demoen en gjenbruksmal
eller en bestemt komprimeringspakke uten bevis.

Tallene under kommer fra forfatterens tokensvar, også lest direkte på X.
`in` tolkes som uncached input separat fra cached, slik oppstillingen angir.
Det er kumulative summer over requests, ikke størrelsen på ett kontekstvindu.

| Oppsett | Uncached input | Cached input | Output | Cacheandel av input | Totalt |
|---|---:|---:|---:|---:|---:|
| DS lead + Astra | 585 215 | 69 182 720 | 441 206 | 99,16% | 70 209 141 |
| Astra lead + DS | 622 717 | 26 873 216 | 405 067 | 97,74% | 27 901 000 |
| DS solo | 580 393 | 65 379 584 | 457 163 | 99,12% | 66 417 140 |
| Astra solo | 725 581 | 11 115 264 | 57 774 | 93,87% | 11 898 619 |

Astra solo har 7,91 ganger færre rapporterte outputtokens og 5,58 ganger færre
totale tokens enn DS solo. Dette beviser ikke 7,91 ganger kortere kjøretid eller
bedre kvalitet. Modell, reasoning-innstilling, implementasjon og mulig sammensetning
av output/reasoning er forskjellige. Vi har ikke underfordelingen av output.

### Prisfeilen i Grok-svaret

Brukerens oppgitte DS off-peak-satser er $0,15/M uncached, $0,003/M cached og
$0,60/M output. Disse er brukt som **forutsetning**, ikke uavhengig verifisert her.
Offisiell OpenAI-prisliste [S18], åpnet 2026-09-16, viser Astra Standard short-context:
$10/M input, $1/M cached input, $12,50/M cache writes og $50/M output.

For å kontrollere skjermbildets enkle regnestykke bruker vi $10/$1/$50, uten egen
cache-write-kategori, og originalinnleggets tokentall:

| Oppsett | DS-beløp | Astra-beløp | Beregnet sum |
|---|---:|---:|---:|
| DS lead + Astra | $0,4295 | $14,1255 | **$14,55** |
| Astra lead + DS | $0,2759 | $17,9125 | **$18,19** |
| DS solo | $0,5575 | — | **$0,56** |
| Astra solo | — | $21,2598 | **$21,26** |

Eksempel: Astra solo = `0,725581*10 + 11,115264*1 + 0,057774*50` = $21,259774.
Cache-input alene er $11,115264. Derfor kan totalsummen ikke være $0,18 med disse
satsene. Groks DS-beløp ligger nær regnestykket; Astra-beløpene gjør ikke det.
Skjermbildet har dessuten **543 892** Astra-inputtokens i Astra-lead i stedet for
originalens **549 892**. Det forklarer $18,128 i bildet versus $18,188 her.

Dette er sammenlignbare **prisestimater**, ikke bevis på forfatterens faktiske regning.
Cache writes, short/long-context per request, service tier, eventuelle rabatter,
gatewaypåslag og abonnementsbruk er ikke spesifisert. Den offisielle listen har egne
long-context-satser. Vi kan ikke avgjøre kontekstnivå fra en kumulativ sum på 65M.
I Axiom skal providerens faktiske cost og usage være fasit der de finnes.

### Hva dette endrer i Axiom-anbefalingen

- Optimaliser **dollar, latency og kvalitet hver for seg**, ikke rå tokens alene.
  DS kan bruke langt flere tokens og likevel være mye billigere ved disse prisene.
- Cache er sentralt: stabile prefikser, stabile verktøyskjemaer, per-agent sesjoner
  og korrekt providerstøtte. OpenRouter dokumenterer automatisk sticky routing og
  `session_id` [S2]. Axiom bruker ikke session_id nå, men kan allerede få automatisk
  caching; uten usage vet vi ikke faktisk treffrate. Ikke påstå at caching er av.
- En dyr modell bør få avgrensede oppgaver med høy verdi, ikke all historikk fra alle
  agentene. Prøv DS-ledet arbeid med målrettet Astra-hjelp som en kandidat. Test også
  Astra-lead og solo-varianter; én demo gir ingen universell vinner.
- Kompresjon som bryter cache må vurderes i penger. Illustrasjon med DS-satsene:
  100k cachede tokens koster $0,0003, mens 10k uncached koster $0,0015. 90% færre
  inputtokens kan altså koste fem ganger mer i akkurat den requesten. Senere reuse,
  latency og kvalitet kan likevel gjøre kompresjon riktig.
- 94–99% cacheandel er ikke et selvstendig kvalitetsmål. Man kan oppnå høy prosent
  ved å sende mye unødvendig historikk. Mål også absolutt kostnad og nyttig arbeid.

### Gjenbruk som et separat Axiom-forsøk

For våre egne nye 3D-kartspill bør vi teste **gjenbruk + god avgrensning**:
et testet startprosjekt med renderer, kamera, input, fysikk/kollisjon, rundetelling,
restart, assets og smoke-test. Modellen genererer spillspesifikke valg og endringer.
Regn utvikling og vedlikehold av malen med i amortisert kostnad. Sammenlign ikke en
malbasert kjøring mot fra-null-kjøring uten å oppgi forskjellen. Dette er vårt forslag,
ikke en påstand om hvordan Fateev bygget demoen.

Eksempel på oppgaveprompt til et slikt forsøksoppsett:

> Bygg en spillbar 3D-kartprototype fra den vedlagte, versjonerte malen. Behold
> renderer-, input- og testoppsettet. Lever én bane, spilleren, tre motstandere,
> akselerasjon/brems/styring, synlige kollisjoner, korrekt checkpoint-basert
> rundetelling, målgang og restart. Først spillbar kjernesløyfe, deretter visuell
> polish. Planen skal angi grensesnitt og akseptansekriterier uten å beskrive
> eksisterende filer på nytt. Les outline og relevante symboler, bruk små patcher,
> og bruk runnerens målinger som grunnlag for feilretting. Rapportér faktisk utførte
> tester og uverifisert atferd separat. Ingen multiplayer, konto eller track editor.

Dette er en kandidat som skal evalueres, ikke en dokumentert «magisk prompt».
Kort sluttprosa må ikke forveksles med utilstrekkelig tenking eller ufullstendig kode.

## 5. Prioritert gjennomføring og måleplan

| Rekkefølge | Endring | Hva forsøket må vise |
|---|---|---|
| 1 | Behold usage/generation-ID, per-role ledger, prompt- og harnessversjoner | Provider-totalsummer kan avstemmes; retries og feil er synlige; ingen dobbeltelling |
| 2 | Line/symbol reads, repo-kart, korte overleveringer, fjern duplisert repair-context | Lavere kumulative inputtokens uten flere gjeninnlesinger eller tapte krav |
| 3 | Gjenbruk hashbundet statisk verifisering, batch uavhengige operasjoner | Færre modellrunder og kortere tid, like gode eller bedre feilfunn |
| 4 | Isolert execution + browser-smoke og korte feilrapporter | Flere faktisk kjørbare leveranser og færre dyre videreføringer |
| 5 | Test selektiv Planner/Tester/Reviewer, per-role model/effort | Bedre kostnad/tid per god leveranse enn dagens fire-rolle-baseline |
| 6 | A/B-test Graphify/Serena/Headroom og eventuelt Deep Agents-deler | Netto gevinst etter indeksering, retrieval, cachetap og lokal overhead |
| 7 | GEPA på et lite treningssett, holdout for godkjenning | Forbedring på ukjente oppgaver; opptjeningspunkt for optimaliseringskostnaden |

Bruk minst tre oppgavetyper: ny liten app, avgrenset endring i eksisterende kode,
og 3D-kartspill. Skill fra-null og malbaserte oppgaver. Behold separate kontrolloppgaver
for vanskelig feilretting. Start med 3 gjentakelser per variant for screening, utvid
ved små forskjeller; ikke trekk statistisk sterke konklusjoner fra tre runs.

Frys oppgavetekst, startfiler, dependencyversjoner, eksakte modeller der tilgjengelig,
runner og akseptansekriterier. Skill kald og varm cache, veksle forsøksrekkefølge og
tell alle feilforsøk. Sammenlign først én endring om gangen; test vinnerkombinasjonen
etterpå. Nye modellkjøringer bør få et eksplisitt kostnadstak før de startes.

Mål input, cached input, output/reasoning, total faktisk kostnad, modellkall,
wall-clock inklusive venting/bygging/indeksering, repair-runder, krasj, oppfylte krav,
og blindvurdert visuell/spillbar kvalitet. Behold både første-forsøk-score og totalscore
etter repair. `approved` fra egen Reviewer er ikke fasiten.

For kartspillet: kald oppstart, ingen manglende imports/assets, input virker,
kamera følger, kollisjon/restart fungerer, runder krever riktige checkpoints,
motstandere beveger seg, og en dokumentert frame-time-måling på fast maskin/oppsett.
Gi visuell kvalitet egen vurdering slik at minimale, kjedelige spill ikke vinner bare
fordi de er billige. Mål også kostnad per bestått oppgave over hele forsøkssettet.

Ambisjon, ikke prognose: undersøk om vi kan kutte 40–60% kumulative inputtokens
og 20–40% wall-clock på videreføringer uten svekket kvalitet. Gjenbruksmaler kan ha
større effekt på nybygg, men dette er en annen sammenligning. Resultatet kan også være
at flere tokens i Coder gir lavere total kostnad fordi Tester/repair slipper arbeid.

## 6. Rettelser gjort i denne gjennomgangen

- Avvist continuation publiserer ikke lenger en foreldreløs queued-oppgave.
- SQLite UPSERT beholder rowid, så fremtidige oppdateringer ikke stokker Recent tasks.
  Dette rekonstruerer ikke den historiske rekkefølgen fra før rettelsen.
- Løkkebeskyttelsen skiller fullstendige verktøyargumenter, slik at forskjellige
  redigeringer i samme fil og forskjellige worker-ID-er ikke blandes sammen.
  Identiske kall er fortsatt begrenset. Fremtidig forbedring: skill også filversjoner
  ved gjentatt legitim lesing; slik fremdriftsmåling er ikke implementert her.
- ESLint hopper over Python-miljø, pytest-cache og runtime-workspaces.
- Handoff-navnet er endret fra Astra til Axiom, med oppdaterte referanser.

Tre regresjonstester feilet før de tilhørende rettelsene og passerer etterpå.
Hele backend-settet: 70 passed; TypeScript og ESLint passerer. Én eksisterende
Starlette/AnyIO deprecation warning gjenstår. Ingen ny ekte spillkjøring er utført.
Feilede continuation-kopier kan fortsatt etterlate en delvis workspace-mappe på disk;
rettelsen hindrer en fastlåst databaseoppgave, men innfører ikke automatisk sletting.

## Kilder lest

Alle hentet 2026-09-16. README-er er opphavspersonenes dokumentasjon, ikke uavhengige
replikasjoner. Lisensangivelser gjelder den inspiserte versjonen; kontroller valgte
versjoner ved faktisk integrasjon.

- S1: [OpenRouter usage accounting](https://github.com/OpenRouterTeam/docs/blob/main/cookbook/administration/usage-accounting.mdx)
- S2: [OpenRouter prompt caching og sticky sessions](https://github.com/OpenRouterTeam/docs/blob/main/guides/best-practices/prompt-caching.mdx)
- S3: [OpenRouter reasoning og outputtak](https://github.com/OpenRouterTeam/docs/blob/main/guides/best-practices/reasoning-tokens.mdx)
- S4: [Langfuse SDK](https://github.com/langfuse/langfuse-python), [usage/cost](https://github.com/langfuse/langfuse-docs/blob/main/content/docs/observability/features/token-and-cost-tracking.mdx), [LangGraph](https://github.com/langfuse/langfuse-docs/blob/main/content/integrations/frameworks/langgraph.mdx), [compose](https://github.com/langfuse/langfuse/blob/main/docker-compose.yml), [lisens](https://github.com/langfuse/langfuse/blob/main/LICENSE)
- S5: [Aider repo-map-dokumentasjon](https://github.com/Aider-AI/aider/blob/main/aider/website/docs/repomap.md), [implementasjon](https://github.com/Aider-AI/aider/blob/main/aider/repomap.py)
- S6: [Serena README og komponentlisenser](https://github.com/oraios/serena)
- S7: [Graphify README](https://github.com/Graphify-Labs/graphify), [benchmarkmetode og resultater](https://github.com/Graphify-Labs/graphify/blob/v8/BENCHMARKS.md)
- S8: [Headroom mekanismer og benchmarkbegrensninger](https://github.com/headroomlabs-ai/headroom)
- S9: [Deep Agents README](https://github.com/langchain-ai/deepagents)
- S10: [RTK README og definisjon av savings](https://github.com/rtk-ai/rtk)
- S11: [GEPA](https://github.com/gepa-ai/gepa), [DSPy](https://github.com/stanfordnlp/dspy)
- S12: [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent)
- S13: [Context Mode](https://github.com/mksglu/context-mode)
- S14: [DCP, cache tradeoffs og prosjektstatus](https://github.com/Opencode-DCP/opencode-dynamic-context-pruning)
- S15: [LLMLingua](https://github.com/microsoft/LLMLingua)
- S16: [Originaldemo og metode](https://x.com/superalesha/status/2099836448044175427), [tokenregnskap](https://x.com/superalesha/status/2099881209555726827)
- S17: [Spillrepo](https://github.com/alesha-pro/bench-portal), særlig `games/kart-{astra-solo,deepseek-solo,astra-deepseek,deepseek-astra}/game.json`
- S18: [Offisiell OpenAI-prisliste](https://developers.openai.com/api/docs/pricing)
