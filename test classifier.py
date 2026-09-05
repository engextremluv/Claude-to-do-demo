from nhs_sponsor_alert import classify_sponsorship, title_ok, field_ok

A = "We welcome applications from candidates who require Skilled Worker Visa sponsorship to work in the UK, and these will be considered alongside all other applications in line with our commitment to equality and inclusion."
B = ("Please be advised we are unable to provide Skilled Worker Visa Sponsorship to non-UK residents. "
     "Applications from job seekers who require current Skilled worker sponsorship to work in the UK are "
     "welcome and will be considered alongside all other applications.")
C = "Under current Home Office guidance, it is strongly unlikely that the criteria required to support Skilled Worker visa sponsorship will be met for this vacancy."
D = "Ensure you hold valid right to work in the UK and will not require sponsorship if successful."
E = "Applications from job seekers who require current Skilled worker sponsorship to work in the UK are welcome and will be considered alongside all other applications."

ok = True
for name, text, exp in [("A confirmed",A,"yes"),("B unable+footer",B,"no"),
                        ("C unlikely",C,"no"),("D will not require",D,"no"),("E footer-only",E,"unknown")]:
    v, ph = classify_sponsorship(text)
    flag = "PASS" if v == exp else "FAIL"; ok &= (v == exp)
    print(f"[{flag}] {name:18s} -> {v:7s} (expected {exp})")

# Widened title filter: healthcare/medical kept, corporate dropped.
titles = [
    ("Band 3 Healthcare Assistant", True), ("Nursing Associate", True),
    ("Pharmacy Assistant", True), ("Phlebotomist", True),
    ("Occupational Therapy Assistant", True), ("Maternity Support Worker", True),
    ("Mental Health Support Worker", True), ("Biomedical Support Worker", True),
    ("Finance Business Partner", False), ("IT Service Desk Analyst", False),
    ("HR Advisor", False), ("Procurement Officer", False),
]
for t, exp in titles:
    r = title_ok(t); flag = "PASS" if r == exp else "FAIL"; ok &= (r == exp)
    print(f"[{flag}] title {t!r:42s} -> {r}")

# Hours: "Full time" matches, part time does not.
for val, exp in [("Full time", True), ("Full time - 37.5 hours per week", True),
                 ("Full time - 36 hours per week", True), ("Part time - 20 hours", False)]:
    r = field_ok(val, None, contains_list=["full time"]); flag = "PASS" if r == exp else "FAIL"; ok &= (r == exp)
    print(f"[{flag}] hours {val!r:34s} -> {r}")

print("\nALL PASS" if ok else "\nSOME FAILED")
