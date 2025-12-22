from typing import List, Optional

import json
from collections import defaultdict
from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from pretix.base.models import SeatCategoryMapping, SeatingPlan
from pretix.base.models.seating import SeatingPlanLayoutValidator
from pretix.base.services import seating as seating_service


class SeatingPlanSettingsForm(forms.Form):
    plan_name = forms.CharField(
        label=_("Seating plan name"),
        required=False,
        max_length=190,
        help_text=_("Shown internally to help you keep multiple plans apart."),
    )
    plan_file = forms.FileField(
        label=_("Seating plan (.json)"),
        required=False,
        help_text=_("Upload the JSON export from the pretix Seating Plan maker."),
    )

    def __init__(self, *args, **kwargs):
        event = kwargs.pop("event", None)
        obj = kwargs.pop("obj", None)
        self.event = event or obj
        if self.event is None:
            raise ValueError("event or obj must be provided")
        super().__init__(*args, **kwargs)
        self._plan_data: Optional[dict] = None
        self._plan_document: Optional[str] = None
        self._category_names: List[str] = []
        self._validator = SeatingPlanLayoutValidator()
        categories = self._read_categories_from_upload()
        if categories is None:
            categories = self._current_categories()
        self._category_names = categories
        self._add_category_fields(categories)
        if not self.is_bound:
            stored_name = self.event.settings.get("quse_seatingplan_name")
            self.initial["plan_name"] = stored_name or (
                self.event.seating_plan.name
                if self.event.seating_plan
                else self.event.name
            )

    def _read_categories_from_upload(self) -> Optional[List[str]]:
        plan_file = self.files.get("plan_file")
        if not plan_file:
            return None
        uploaded = plan_file.read()
        plan_file.seek(0)
        try:
            decoded = uploaded.decode("utf-8")
            data = json.loads(decoded)
        except (UnicodeDecodeError, ValueError):
            # Let Django surface a meaningful error in clean_plan_file.
            return None
        self._plan_document = decoded
        self._plan_data = data
        return [c["name"] for c in data.get("categories", [])]

    def _current_categories(self) -> List[str]:
        if self.event.seating_plan:
            return [c.name for c in self.event.seating_plan.get_categories()]
        return []

    def _add_category_fields(self, categories: List[str]) -> None:
        mapping_rows = SeatCategoryMapping.objects.filter(
            event=self.event, subevent=None
        )
        mappings = defaultdict(list)
        for mapping in mapping_rows:
            mappings[mapping.layout_category].append(mapping.product_id)
        item_qs = self.event.items.all().order_by("category__position", "name")
        for category in categories:
            field_name = self._category_field_name(category)
            self.fields[field_name] = forms.ModelMultipleChoiceField(
                label=_('Products for "%(category)s"') % {"category": category},
                queryset=item_qs,
                required=False,
                help_text=_(
                    "Select one or more products that may sell seats in this category."
                ),
            )
            if not self.is_bound and mappings.get(category):
                self.initial[field_name] = mappings[category]

    @staticmethod
    def _category_field_name(name: str) -> str:
        return f"category__{name}"

    def clean_plan_file(self) -> Optional[str]:
        upload = self.cleaned_data["plan_file"]
        if not upload:
            return None
        if not self._plan_data or not self._plan_document:
            raw = upload.read()
            upload.seek(0)
            try:
                decoded = raw.decode("utf-8")
                data = json.loads(decoded)
            except (UnicodeDecodeError, ValueError) as exc:
                raise forms.ValidationError(
                    _("Could not decode JSON: %(error)s"), params={"error": exc}
                )
            self._plan_document = decoded
            self._plan_data = data
        try:
            self._validator(self._plan_data)
        except ValidationError as exc:
            raise forms.ValidationError(exc)
        return self._plan_document

    def clean(self):
        cleaned = super().clean()
        if not self.event.seating_plan and not (
            self._plan_data or cleaned.get("plan_file")
        ):
            raise forms.ValidationError(_("Upload a seating plan file before saving."))
        plan = self._plan_for_validation(cleaned.get("plan_name"))
        if plan:
            try:
                seating_service.validate_plan_change(self.event, None, plan)
            except seating_service.SeatProtected as exc:
                raise forms.ValidationError(str(exc))
        return cleaned

    def save(self) -> SeatingPlan:
        plan_name = self.cleaned_data.get("plan_name") or self.event.name
        plan = self.event.seating_plan
        if self._plan_data:
            if not plan:
                plan = SeatingPlan(organizer=self.event.organizer)
            plan.layout_data = self._plan_data
            plan.name = plan_name
            plan.save()
        elif plan and plan.name != plan_name:
            plan.name = plan_name
            plan.save(update_fields=["name"])
        if not plan:
            raise forms.ValidationError(_("No seating plan is available."))
        if self.event.seating_plan_id != plan.id:
            self.event.seating_plan = plan
            self.event.save(update_fields=["seating_plan"])

        mapping = self._persist_category_mapping()
        seating_service.generate_seats(self.event, None, plan, mapping)

        self.event.settings.quse_seatingplan_checkout_enabled = True
        self.event.settings.quse_seatingplan_name = plan_name
        self.event.settings.seating_choice = False
        return plan

    def _plan_for_validation(self, plan_name: Optional[str]) -> Optional[SeatingPlan]:
        target_name = plan_name or self.event.name
        if self._plan_data:
            plan = SeatingPlan(organizer=self.event.organizer, name=target_name)
            plan.layout_data = self._plan_data
            return plan
        return self.event.seating_plan

    def _persist_category_mapping(self) -> dict:
        mapping = {}
        seen = set()
        for category in self._category_names:
            seen.add(category)
            products = list(
                self.cleaned_data.get(self._category_field_name(category)) or []
            )
            SeatCategoryMapping.objects.filter(
                event=self.event,
                subevent=None,
                layout_category=category,
            ).delete()
            if products:
                SeatCategoryMapping.objects.bulk_create(
                    [
                        SeatCategoryMapping(
                            event=self.event,
                            subevent=None,
                            layout_category=category,
                            product=product,
                        )
                        for product in products
                    ]
                )
                if len(products) == 1:
                    mapping[category] = products[0]
        SeatCategoryMapping.objects.filter(event=self.event, subevent=None).exclude(
            layout_category__in=seen
        ).delete()
        return mapping
