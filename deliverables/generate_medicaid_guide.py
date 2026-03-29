"""Generate the Medicaid MCO Reference Guide PDF — companion to the Excel file."""

from fpdf import FPDF
from pathlib import Path

OUTPUT = Path(__file__).parent / "Medicaid_MCO_Reference_Guide.pdf"


class GuidePDF(FPDF):
    def header(self):
        if self.page_no() == 1:
            return  # custom header on page 1
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 5, "Medicaid Managed Care Plan Reference Guide", align="R")
        self.ln(8)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")

    def section_heading(self, text):
        self.set_font("Helvetica", "B", 13)
        self.set_text_color(47, 84, 150)
        self.cell(0, 10, text)
        self.ln(8)
        # underline
        self.set_draw_color(47, 84, 150)
        self.set_line_width(0.5)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(4)

    def body_text(self, text):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(40, 40, 40)
        self.multi_cell(0, 5.5, text)
        self.ln(2)

    def bold_body(self, text):
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(40, 40, 40)
        self.multi_cell(0, 5.5, text)
        self.ln(1)

    def definition_row(self, term, definition):
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(40, 40, 40)
        x_start = self.l_margin
        term_w = 42
        self.set_x(x_start)
        self.cell(term_w, 5.5, term)
        self.set_font("Helvetica", "", 10)
        self.multi_cell(self.w - self.l_margin - self.r_margin - term_w, 5.5, definition)
        self.ln(1.5)

    def column_row(self, col_name, description):
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(40, 40, 40)
        col_w = 48
        self.cell(col_w, 5.5, col_name)
        self.set_font("Helvetica", "", 10)
        self.multi_cell(self.w - self.l_margin - self.r_margin - col_w, 5.5, description)
        self.ln(1)



def build():
    pdf = GuidePDF()
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # ---- Title ----
    pdf.set_font("Helvetica", "B", 22)
    pdf.set_text_color(47, 84, 150)
    pdf.cell(0, 12, "Medicaid Managed Care", align="C")
    pdf.ln(10)
    pdf.cell(0, 12, "Plan Reference Guide", align="C")
    pdf.ln(14)

    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 6, "Companion guide to Medicaid_MCO_Reference_Table.xlsx", align="C")
    pdf.ln(5)
    pdf.cell(0, 6, "Data Year: 2024  |  Source: CMS data.medicaid.gov  |  Prepared March 2026", align="C")
    pdf.ln(12)

    # ---- What Is This? ----
    pdf.section_heading("What Is This?")
    pdf.body_text(
        "The accompanying Excel spreadsheet contains every Medicaid managed care plan "
        "reported to CMS as of July 2024 -- 2,184 plans across all 50 states and DC. "
        "This guide explains what's in it and how to use it."
    )
    pdf.body_text(
        "If you've ever needed to answer \"Is this payor a Medicaid MCO?\" or "
        "\"What kind of Medicaid plan is this?\", this spreadsheet is your reference."
    )

    # ---- Spreadsheet Columns ----
    pdf.section_heading("Spreadsheet Columns Explained")

    pdf.column_row("State", "Two-letter state abbreviation (e.g., TX, NY, CA).")
    pdf.column_row("Plan Name", "The plan's registered name with CMS. Note: this may not match the name your organization uses -- payors often operate under trade names, DBAs, or clearinghouse names that differ from their CMS registration.")
    pdf.column_row("Plan Year", "The data year (currently 2024 across all rows).")
    pdf.column_row("Plan Type", "The type of managed care arrangement. See the Plan Types section below for details.")
    pdf.column_row("Benefit Category",
        "What services the plan covers: Comprehensive (full-scope), or a carve-out "
        "category like Dental, Behavioral Health, etc. See the Benefit Categories section below.")
    pdf.column_row("Program Type", "Whether the plan serves Medicaid, CHIP, or both populations.")
    pdf.column_row("Parent Organization",
        "The corporate parent (e.g., Centene, UnitedHealth Group, Molina). "
        "Note: CMS does not consistently report this field -- about 35% of rows are blank. "
        "A blank here does not mean the plan is independent.")
    pdf.column_row("CMS Program Name", "The raw program name from CMS. Often includes the state's program branding (e.g., \"STAR\" in TX, \"MassHealth\" in MA). Useful for context but not for filtering.")
    pdf.column_row("Geographic Region", "The plan's service area within the state. Can be a single region or a long list of counties.")
    pdf.column_row("Medicaid Enrollment", "Number of Medicaid-only members enrolled as of July 2024.")
    pdf.column_row("Dual Enrollment", "Number of dual-eligible members (enrolled in both Medicare and Medicaid).")
    pdf.column_row("Total Enrollment", "Sum of Medicaid + Dual enrollment.")

    # ---- Plan Types ----
    pdf.section_heading("Plan Types")
    pdf.body_text("Each plan falls into one of these CMS-defined managed care arrangement types:")
    pdf.ln(1)

    pdf.definition_row("MCO",
        "Managed Care Organization (1,203 plans). Full-service health plans that contract "
        "with states to provide comprehensive or carved-out Medicaid benefits.")
    pdf.definition_row("PAHP",
        "Prepaid Ambulatory Health Plan (145 plans). Covers outpatient/ambulatory services "
        "only. Common for dental and transportation carve-outs.")
    pdf.definition_row("BHO",
        "Behavioral Health Organization (148 plans). Specialized plans covering mental "
        "health and substance use disorder services.")
    pdf.definition_row("PIHP",
        "Prepaid Inpatient Health Plan (115 plans). Covers inpatient services and may also "
        "cover outpatient. Often used for behavioral health carve-outs.")
    pdf.definition_row("PACE",
        "Program of All-Inclusive Care for the Elderly (473 plans). Community-based programs "
        "for adults 55+ who qualify for nursing home care. Each PACE site is a separate row.")
    pdf.definition_row("PCCM",
        "Primary Care Case Management (70 plans). A model where the state pays PCPs a "
        "per-member fee to coordinate care. Not a health plan per se.")
    pdf.definition_row("DSNP",
        "Dual-Eligible Special Needs Plan (30 plans). Medicare Advantage plans specifically "
        "for dual-eligible members that also have Medicaid contracts.")

    # ---- Benefit Categories ----
    pdf.section_heading("Benefit Categories")
    pdf.body_text(
        "States structure Medicaid differently. Some contract with one MCO to cover everything; "
        "others carve out specific benefits to specialized plans. The Benefit Category column tells you "
        "which model applies to each plan."
    )
    pdf.ln(1)

    pdf.definition_row("Comprehensive",
        "Full-scope Medicaid managed care. Covers primary care, specialists, inpatient/outpatient, "
        "labs, imaging, and usually pharmacy. When you're trying to confirm a payor is a \"Medicaid MCO,\" "
        "this is the category you're looking for.")
    pdf.definition_row("Long-Term Care",
        "Long-term services and supports (LTSS) -- nursing facilities, home & community-based services, "
        "managed long-term care (MLTC/MLTSS). Separate from comprehensive medical.")
    pdf.definition_row("Behavioral Health",
        "Carved-out mental health and substance use disorder (SUD) coverage. Managed by specialized "
        "BHOs or PIHPs in states that separate BH from medical.")
    pdf.definition_row("Transportation",
        "Non-emergency medical transportation (NEMT). Federal law requires states to offer rides to "
        "Medicaid appointments. Common vendors: ModivCare, LogistiCare, MTM.")
    pdf.definition_row("Dental",
        "Dental benefits managed separately from the comprehensive MCO. Plans like DentaQuest "
        "frequently appear here.")
    pdf.definition_row("Pharmacy",
        "Pharmacy carve-outs. Rare -- most states include pharmacy in the MCO contract or manage "
        "it fee-for-service.")
    pdf.definition_row("Vision",
        "Vision benefit carve-outs. Uncommon but present in some states.")

    # ---- Data Source ----
    pdf.section_heading("Data Source")
    pdf.body_text(
        "CMS Medicaid Managed Care Enrollment Report, Table 4 (Enrollment by Program and Plan). "
        "Published annually at data.medicaid.gov. The enrollment snapshot reflects enrollment as of "
        "July 1, 2024. The 2025 update has not yet been published by CMS."
    )
    pdf.body_text(
        "Benefit categories were derived using CMS program names, plan names, and a curated list of "
        "known carve-out vendors. Classification methodology is documented separately."
    )

    pdf.output(str(OUTPUT))
    print(f"Saved to {OUTPUT}")


if __name__ == "__main__":
    build()
