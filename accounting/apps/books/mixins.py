from django.db.models.fields import FieldDoesNotExist
from django.views import generic
from django.http import HttpResponseRedirect
from django.core.urlresolvers import reverse

from .utils import organization_manager
from .forms import PaymentForm

class RestrictToSelectedOrganizationQuerySetMixin(object):
    """
    To restrict objects to the current selected organization
    """

    def get_restriction_filters(self):
        # check for the field
        f = self.model._meta.get_field('organization')
        field = f.name
        m = f.model
        if not f.auto_created or f.concrete:
            direct = f
        m2m = f.many_to_many

        # build the restriction
        orga = organization_manager.get_selected_organization(self.request)
        return {field: orga.pk}

    def get_queryset(self):
        filters = self.get_restriction_filters()
        queryset = super(RestrictToSelectedOrganizationQuerySetMixin, self).get_queryset()
        queryset = queryset.filter(**filters)
        return queryset

    def get(self, request, *args, **kwargs):
        orga = organization_manager.get_selected_organization(request)
        if orga is None:
            return HttpResponseRedirect(reverse('books:organization-selector'))
        return super(RestrictToSelectedOrganizationQuerySetMixin, self).get(request, *args, **kwargs)


class RestrictToOrganizationFormRelationsMixin(object):
    """
    To restrict relations choices to the organization linked instances
    """
    relation_name = 'organization'

    def _restrict_fields_choices(self, model, organization, fields):
        for source in fields:
            f = model._meta.get_field(source)
            field = f.name
            m = f.model
            if not f.auto_created or f.concrete:
                direct = f
            m2m = f.many_to_many
            rel = f.remote_field
            if not rel:
                # next field
                continue

            rel_model = rel.to
            try:
                rel_model._meta.get_field(self.relation_name)
            except FieldDoesNotExist:
                # next field
                continue

            form_field = fields[source]
            form_field.queryset = (form_field.choices.queryset
                .filter(**{self.relation_name: organization}))

    def restrict_fields_choices_to_organization(self, form, organization):
        assert organization is not None, "no organization to restrict to"
        model = form._meta.model
        self._restrict_fields_choices(model, organization, form.fields)


class SaleListQuerySetMixin(object):

    def get_queryset(self):
        queryset = super(SaleListQuerySetMixin, self).get_queryset()
        queryset = (queryset
            .select_related(
                'organization')
            .prefetch_related(
                'lines',
                'lines__tax_rate'))

        try:
            # to raise the exception
            self.model._meta.get_field('client')
            queryset = queryset.select_related('client')
        except FieldDoesNotExist:
            pass

        try:
            # to raise the exception
            self.model._meta.get_field('payments')
            queryset = queryset.prefetch_related('payments')
        except FieldDoesNotExist:
            pass

        return queryset


class AutoSetSelectedOrganizationMixin(object):

    def form_valid(self, form):
        obj = form.save(commit=False)
        orga = organization_manager.get_selected_organization(self.request)
        obj.organization = orga

        return super(AutoSetSelectedOrganizationMixin, self).form_valid(form)


class AbstractSaleCreateUpdateMixin(RestrictToOrganizationFormRelationsMixin,
                                    object):
    formset_class = None

    def get_context_data(self, **kwargs):
        assert self.formset_class is not None, "No formset class specified"
        context = super(AbstractSaleCreateUpdateMixin, self).get_context_data(**kwargs)
        orga = organization_manager.get_selected_organization(self.request)
        if self.request.POST:
            context['line_formset'] = self.formset_class(
                self.request.POST,
                instance=self.object,
                organization=orga)
        else:
            context['line_formset'] = self.formset_class(
                instance=self.object,
                organization=orga)
        return context

    def get_form(self, form_class=None):
        """Restrict the form relations to the current organization"""
        form = super(AbstractSaleCreateUpdateMixin, self).get_form(form_class)
        orga = organization_manager.get_selected_organization(self.request)
        self.restrict_fields_choices_to_organization(form, orga)
        return form

    def form_valid(self, form):
        context = self.get_context_data()
        line_formset = context['line_formset']
        if not line_formset.is_valid():
            return super(AbstractSaleCreateUpdateMixin, self).form_invalid(form)

        self.object = form.save()
        line_formset.instance = self.object
        line_formset.save()

        # update totals
        self.object.compute_totals()

        return super(AbstractSaleCreateUpdateMixin, self).form_valid(form)


class AbstractSaleDetailMixin(object):

    def get_queryset(self):
        queryset = super(AbstractSaleDetailMixin, self).get_queryset()
        queryset = queryset.select_related('organization')

        try:
            # to raise the exception
            self.model._meta.get_field('client')
            queryset = queryset.select_related('client')
        except FieldDoesNotExist:
            pass

        return queryset

    def get_object(self):
        # save some db queries by caching the fetched object
        if hasattr(self, '_object'):
            return getattr(self, '_object')

        obj = super(AbstractSaleDetailMixin, self).get_object()
        setattr(self, '_object', obj)
        return obj

    def get_context_data(self, **kwargs):
        ctx = super(AbstractSaleDetailMixin, self).get_context_data(**kwargs)
        obj = self.get_object()
        ctx["checklist"] = obj.full_check()
        ctx["lines"] = (obj.lines.all()
            .select_related(
                'tax_rate'))
        return ctx


class PaymentFormMixin(generic.edit.FormMixin):
    payment_form_class = None
    form_class = PaymentForm

    def get_context_data(self, **kwargs):
        assert self.payment_form_class is not None, \
            "No formset class specified"
        self.object = self.get_object()
        form = self.get_form(self.payment_form_class)
        context = super(PaymentFormMixin, self).get_context_data(**kwargs)
        context['payment_form'] = form
        return context

    def post(self, request, *args, **kwargs):
        """
        Handles POST requests, instantiating a form instance with the passed
        POST variables and then checked for validity.
        """
        form = self.get_form(self.payment_form_class)
        if form.is_valid():
            return self.form_valid(form)
        else:
            return self.form_invalid(form)

    def form_valid(self, form):
        self.object = self.get_object()

        # save payment
        payment = form.save(commit=False)
        payment.content_object = self.object
        payment.save()
        return super(PaymentFormMixin, self).form_valid(form)
