# CaseTracker — municipal case-status overlay
# Copyright 2026 VoiceLessQ — https://github.com/VoiceLessQ
# Licensed under the Apache License 2.0; see LICENSE and NOTICE.
"""Security-path tests for the people app: append-only notes, note-visibility
enforcement, and the document access gate."""
from django.contrib.auth import get_user_model
from django.test import TestCase

from org.models import Department, Membership
from people.access import can_open_person_documents, visible_person_notes
from people.models import Person, PersonAccessGrant, PersonNote

User = get_user_model()


class AppendOnlyPersonNoteTests(TestCase):
    def test_note_cannot_be_updated_or_deleted(self):
        author = User.objects.create_user("author")
        person = Person.objects.create(name="Test Person")
        note = PersonNote.objects.create(person=person, author=author, text="first")

        note.text = "rewritten"
        with self.assertRaises(ValueError):
            note.save()
        with self.assertRaises(ValueError):
            note.delete()
        # The original row is untouched.
        self.assertEqual(PersonNote.objects.get(pk=note.pk).text, "first")


class NoteVisibilityTests(TestCase):
    def setUp(self):
        self.dept_a = Department.objects.create(code="a", name="Dept A")
        self.dept_b = Department.objects.create(code="b", name="Dept B")
        self.author = User.objects.create_user("author")
        Membership.objects.create(user=self.author, department=self.dept_a)
        self.peer = User.objects.create_user("peer")          # shares dept A with author
        Membership.objects.create(user=self.peer, department=self.dept_a)
        self.outsider = User.objects.create_user("outsider")  # dept B only
        Membership.objects.create(user=self.outsider, department=self.dept_b)

        self.person = Person.objects.create(name="Subject")
        self.dept_note = PersonNote.objects.create(
            person=self.person, author=self.author, text="dept-only",
            visibility=PersonNote.Visibility.DEPARTMENT,
        )
        self.all_note = PersonNote.objects.create(
            person=self.person, author=self.author, text="all-staff",
            visibility=PersonNote.Visibility.ALL_STAFF,
        )

    def _ids(self, user):
        return set(visible_person_notes(user).values_list("id", flat=True))

    def test_author_sees_both(self):
        self.assertEqual(self._ids(self.author), {self.dept_note.id, self.all_note.id})

    def test_same_department_peer_sees_department_note(self):
        self.assertIn(self.dept_note.id, self._ids(self.peer))

    def test_outsider_sees_only_all_staff_note(self):
        ids = self._ids(self.outsider)
        self.assertIn(self.all_note.id, ids)
        self.assertNotIn(self.dept_note.id, ids)

    def test_superuser_sees_everything(self):
        su = User.objects.create_superuser("root", "root@example.com", "pw-not-real")
        self.assertEqual(visible_person_notes(su).count(), 2)


class DocumentAccessGateTests(TestCase):
    def test_shielding_gate(self):
        worker = User.objects.create_user("worker")
        normal = Person.objects.create(name="Normal")
        shielded = Person.objects.create(name="Shielded", is_shielded=True)

        # Non-shielded: navigation scope governs, open is allowed.
        self.assertTrue(can_open_person_documents(worker, normal))
        # Shielded without a grant: blocked even though the worker can navigate.
        self.assertFalse(can_open_person_documents(worker, shielded))

        PersonAccessGrant.objects.create(
            person=shielded, user=worker, granted_by=worker, reason="audit test",
        )
        self.assertTrue(can_open_person_documents(worker, shielded))
