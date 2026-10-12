import io

from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import Workbook

from academics.models import Semester
from accounts.models import User
from config.testutils import BaseTestCase
from instructors import masterlist
from instructors.masterlist import MasterlistError
from instructors.models import SemesterAccount, Instructor

HEADER = "username,first_name,middle_name,last_name,extension_name\n"


def csv_upload(body, name="masterlist.csv"):
    return SimpleUploadedFile(name, (HEADER + body).encode("utf-8"))


def xlsx_upload(rows, name="masterlist.xlsx"):
    wb = Workbook()
    ws = wb.active
    ws.append(masterlist.COLUMNS)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return SimpleUploadedFile(name, buf.getvalue())


class MasterlistValidationTests(BaseTestCase):
    def validate(self, body):
        return masterlist.validate_rows(masterlist.parse_upload(csv_upload(body)))

    def test_valid_rows(self):
        rows = self.validate("2026-001,Juan,S,Cruz,Jr.\n2026-002,Ana,,Lim," + chr(10))
        self.assertTrue(all(r.ok for r in rows))
        self.assertEqual([r.status for r in rows], ["new", "new"])
        self.assertEqual(rows[0].line, 2)

    def test_missing_required_fields(self):
        r = self.validate(",Juan,,," + chr(10))[0]
        self.assertEqual(r.status, "error")
        self.assertIn("Missing username", r.errors)
        self.assertIn("Missing last_name", r.errors)

    def test_duplicate_usernames_in_file(self):
        rows = self.validate("A1,One,,X," + chr(10) + "A1,Two,,Y," + chr(10))
        self.assertEqual(rows[0].status, "new")
        self.assertEqual(rows[1].status, "error")
        self.assertTrue(any("Duplicate" in e for e in rows[1].errors))

    def test_an_email_column_in_an_old_file_is_ignored(self):
        """No e-mail addresses are stored: an extra 'email' column in the upload is simply skipped."""
        body = "username,first_name,last_name,email" + chr(10) + "A1,One,X,one@example.com" + chr(10)
        rows = masterlist.validate_rows(masterlist.parse_upload(SimpleUploadedFile("old.csv", body.encode())))
        self.assertEqual(rows[0].status, "new")
        self.assertNotIn("email", rows[0].data)

    def test_old_files_with_instructor_number_and_department_still_import(self):
        """Older masterlists used 'instructor_number' for the login name and had a 'department' column: both are tolerated."""
        body = "instructor_number,first_name,last_name,department" + chr(10) + "A7,One,X,ITE" + chr(10)
        rows = masterlist.validate_rows(masterlist.parse_upload(SimpleUploadedFile("old.csv", body.encode())))
        self.assertEqual(rows[0].status, "new")
        self.assertEqual(rows[0].data["username"], "A7")
        self.assertNotIn("department", rows[0].data)

    def test_no_year_or_enrollment_columns_exist(self):
        """Instructors have no year level, enrolment or department: only these columns are used."""
        self.assertEqual(masterlist.COLUMNS, ["username", "first_name", "middle_name", "last_name", "extension_name"])
        rows = masterlist.validate_rows(masterlist.parse_upload(xlsx_upload([["A1", "One", "", "X", ""]])))
        self.assertEqual(rows[0].status, "new")
        self.assertNotIn("year_level", rows[0].data)

    def test_numeric_usernames_from_excel(self):
        rows = masterlist.validate_rows(masterlist.parse_upload(xlsx_upload([[20260001, "One", "", "X", ""]])))
        self.assertEqual(rows[0].data["username"], "20260001")

    def test_existing_instructors_are_flagged_not_errors(self):
        self.basic_setup()
        r = self.validate("s1,Changed,,Name," + chr(10))[0]
        self.assertEqual(r.status, "existing")
        self.assertTrue(r.ok)

    def test_username_colliding_with_admin_username_is_error(self):
        self.make_admin("boss")
        r = self.validate("boss,One,,X," + chr(10))[0]
        self.assertEqual(r.status, "error")

    def test_file_level_errors(self):
        with self.assertRaises(MasterlistError):
            masterlist.parse_upload(SimpleUploadedFile("x.txt", b"hello"))
        with self.assertRaises(MasterlistError):
            masterlist.parse_upload(SimpleUploadedFile("x.csv", b"username,first_name" + bytes([10]) + b"A,B" + bytes([10])))  # missing columns
        with self.assertRaises(MasterlistError):
            masterlist.parse_upload(SimpleUploadedFile("x.csv", b""))
        with self.assertRaises(MasterlistError):
            masterlist.parse_upload(csv_upload(""))  # header only
        with self.assertRaises(MasterlistError):
            masterlist.parse_upload(SimpleUploadedFile("x.xlsx", b"not really excel"))

    def test_blank_lines_ignored_and_headers_normalised(self):
        body = "Username,First Name,Last Name,Year Level" + chr(10) + "A1,One,X,1" + chr(10) + ",,," + chr(10)
        rows = masterlist.parse_upload(SimpleUploadedFile("x.csv", body.encode()))
        self.assertEqual(len(rows), 1)


class MasterlistImportTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.policy = self.make_policy()
        self.semester = self.make_semester()

    def run_import(self, body):
        rows = masterlist.validate_rows(masterlist.parse_upload(csv_upload(body)))
        return masterlist.import_rows([r.data for r in rows if r.ok])

    def test_creates_users_instructors_accounts_and_credentials(self):
        summary = self.run_import("2026-001,Juan,S,Cruz,Jr." + chr(10) + "2026-002,Ana,,Lim," + chr(10))
        self.assertEqual((summary.created, summary.updated, summary.accounts_created), (2, 0, 2))
        s = Instructor.objects.get(user__username="2026-001")
        self.assertTrue(s.user.is_instructor)
        self.assertTrue(s.user.must_change_password)
        self.assertEqual(s.current_account.policy, self.policy)
        self.assertEqual(s.current_account.semester, self.semester)
        cred = next(c for c in summary.credentials if c["username"] == "2026-001")
        self.assertTrue(s.user.check_password(cred["password"]))
        self.assertEqual(len(cred["password"]), 8)
        self.assertNotEqual(cred["password"], s.current_account.hotspot_password)

    def test_reupload_updates_existing_without_resetting_passwords(self):
        first = self.run_import("A1,One,,X," + chr(10))
        s = Instructor.objects.get(user__username="A1")
        pw_hash = s.user.password
        second = self.run_import("A1,Renamed,,X," + chr(10))
        s.refresh_from_db()
        s.user.refresh_from_db()
        self.assertEqual((second.created, second.updated, second.credentials), (0, 1, []))
        self.assertEqual(s.user.first_name, "Renamed")
        self.assertEqual(s.user.password, pw_hash)
        self.assertEqual(SemesterAccount.objects.filter(instructor=s).count(), 1)
        self.assertEqual(len(first.credentials), 1)

    def test_reupload_in_new_semester_creates_new_account(self):
        self.run_import("A1,One,,X," + chr(10))
        new = self.make_semester(active=True, semester=Semester.SECOND)
        summary = self.run_import("A1,One,,X," + chr(10))
        self.assertEqual(summary.accounts_created, 1)
        self.assertEqual(Instructor.objects.get(user__username="A1").accounts.count(), 2)
        self.assertEqual(Instructor.objects.get(user__username="A1").current_account.semester, new)

    def test_import_requires_active_semester_and_policy(self):
        Semester.objects.update(is_active=False)
        with self.assertRaises(MasterlistError):
            self.run_import("A1,One,,X,,ITE\n")
        self.assertFalse(User.objects.filter(username="A1").exists())
        Semester.objects.update(is_active=True)
        self.policy.delete()
        with self.assertRaises(MasterlistError):
            self.run_import("A1,One,,X,,ITE\n")

    def test_imported_password_hash_upgrades_on_login(self):
        summary = self.run_import("A1,One,,X,,ITE\n")
        user = User.objects.get(username="A1")
        self.assertTrue(user.password.startswith("pbkdf2_sha256_bulk$"))
        self.assertTrue(user.check_password(summary.credentials[0]["password"]))

    def test_xlsx_import(self):
        rows = masterlist.validate_rows(masterlist.parse_upload(xlsx_upload([["X1", "One", "", "Y", "", "ITE"]])))
        summary = masterlist.import_rows([r.data for r in rows])
        self.assertEqual(summary.created, 1)
