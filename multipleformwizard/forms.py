from __future__ import unicode_literals

from collections import OrderedDict
from django import forms
from django.contrib.formtools.wizard.storage.exceptions import NoFileStorageConfigured
from django.core.exceptions import ValidationError
from django.core.urlresolvers import reverse
from django.forms import formsets
from django.shortcuts import redirect
from django.utils.translation import ugettext_lazy as _
from django.contrib.formtools.wizard.views import ManagementForm, WizardView as BaseWizardView

import six


class MultipleFormWizardView(BaseWizardView):
    @classmethod
    def get_initkwargs(cls, form_list=None, initial_dict=None,
            instance_dict=None, condition_dict=None, *args, **kwargs):
        """
        Creates a dict with all needed parameters for the form wizard instances.

        * `form_list` - is a list of forms. The list entries can be single form
          classes, tuples of (`step_name`, `form_class`) or tuples of (`step_name`, {`form_name`: `form_class`}).
          If you pass a list of forms, the wizardview will convert the class list to
          (`zero_based_counter`, `form_class`). This is needed to access the
          form for a specific step.
        * `initial_dict` - contains a dictionary of initial data dictionaries.
          The key should be equal to the `step_name` in the `form_list` (or
          the str of the zero based counter - if no step_names added in the
          `form_list`)
        * `instance_dict` - contains a dictionary whose values are model
          instances if the step is based on a ``ModelForm`` and querysets if
          the step is based on a ``ModelFormSet``. The key should be equal to
          the `step_name` in the `form_list`. Same rules as for `initial_dict`
          apply.
        * `condition_dict` - contains a dictionary of boolean values or
          callables. If the value of for a specific `step_name` is callable it
          will be called with the wizardview instance as the only argument.
          If the return value is true, the step's form will be used.
        """

        kwargs.update({
            'initial_dict': initial_dict or kwargs.pop('initial_dict',
                getattr(cls, 'initial_dict', None)) or {},
            'instance_dict': instance_dict or kwargs.pop('instance_dict',
                getattr(cls, 'instance_dict', None)) or {},
            'condition_dict': condition_dict or kwargs.pop('condition_dict',
                getattr(cls, 'condition_dict', None)) or {}
        })

        form_list = form_list or kwargs.pop('form_list',
            getattr(cls, 'form_list', None)) or []

        computed_form_list = OrderedDict()

        assert len(form_list) > 0, 'at least one form is needed'

        # walk through the passed form list
        for i, form in enumerate(form_list):
            if isinstance(form, (list, tuple)):
                # if the element is a tuple, add the tuple to the new created
                # sorted dictionary.

                (step_name, form) = form
                if isinstance(form, dict):
                    form_mapping = form
                    computed_form_list[six.text_type(step_name)] = form_mapping

                elif issubclass(form, forms.Form):
                    computed_form_list[six.text_type(step_name)] = form
            else:
                # if not, add the form with a zero based counter as unicode
                computed_form_list[six.text_type(i)] = form

        # walk through the new created list of forms
        for form in six.itervalues(computed_form_list):
            form_collection = []
            if isinstance(form, dict):
                form_collection = form.values()
            elif issubclass(form, formsets.BaseFormSet):
                # if the element is based on BaseFormSet (FormSet/ModelFormSet)
                # we need to override the form variable.
                form = form.form
                form_collection = [form]

            for form in form_collection:
                # check if any form contains a FileField, if yes, we need a
                # file_storage added to the wizardview (by subclassing).
                for field in six.itervalues(form.base_fields):
                    if (isinstance(field, forms.FileField) and
                            not hasattr(cls, 'file_storage')):
                        raise NoFileStorageConfigured(
                            "You need to define 'file_storage' in your "
                            "wizard view in order to handle file uploads.")

        # build the kwargs for the wizardview instances
        kwargs['form_list'] = computed_form_list
        return kwargs

    def render(self, forms=None, **kwargs):
        """
        Returns a ``HttpResponse`` containing all needed context data.
        """
        forms = forms or self.get_forms()
        context = self.get_context_data(forms=forms, **kwargs)
        return self.render_to_response(context)

    def render_next_step(self, form, **kwargs):
        """
        This method gets called when the next step/form should be rendered.
        `form` contains the last/current form.
        """
        # get the form instance based on the data from the storage backend
        # (if available).
        next_step = self.steps.next
        new_forms = self.get_forms(next_step,
            data=self.storage.get_step_data(next_step),
            files=self.storage.get_step_files(next_step))

        # change the stored current step
        self.storage.current_step = next_step
        return self.render(new_forms, **kwargs)

    def render_goto_step(self, goto_step, **kwargs):
        """
        This method gets called when the current step has to be changed.
        `goto_step` contains the requested step to go to.
        """
        self.storage.current_step = goto_step
        forms = self.get_forms(
            data=self.storage.get_step_data(self.steps.current),
            files=self.storage.get_step_files(self.steps.current))
        return self.render(forms)

    def render_done(self, form, **kwargs):
        """
        This method gets called when all forms passed. The method should also
        re-validate all steps to prevent manipulation. If any form fails to
        validate, `render_revalidation_failure` should get called.
        If everything is fine call `done`.
        """
        final_forms = OrderedDict()
        # walk through the form list and try to validate the data again.
        for form_key in self.get_form_list():
            form_objs = self.get_forms(step=form_key,
                data=self.storage.get_step_data(form_key),
                files=self.storage.get_step_files(form_key))
            final_forms[form_key] = []
            for form_obj in form_objs:
                if not form_obj.is_valid():
                    return self.render_revalidation_failure(form_key, form_obj, **kwargs)
                final_forms[form_key].append(form_obj)

        result_forms = {}
        for form_key in final_forms:
            formcollection = final_forms[form_key]
            if len(formcollection) == 1:
                result_forms[form_key] = formcollection[0]
            elif len(formcollection) > 1:
                # We expect a _tag property
                formcollection_dict = {}
                for form in formcollection:
                    formcollection_dict[form._tag] = form
                    delattr(form, '_tag')
                result_forms[form_key] = formcollection_dict

        # Construct a result list, ordered by step number
        form_list = [result_forms[key] for key in sorted(result_forms.keys())]

        # render the done view and reset the wizard before returning the
        # response. This is needed to prevent from rendering done with the
        # same data twice.
        done_response = self.done(form_list=form_list, form_dict=result_forms, **kwargs)
        self.storage.reset()
        return done_response


    def get(self, request, *args, **kwargs):
        """
        This method handles GET requests.

        If a GET request reaches this point, the wizard assumes that the user
        just starts at the first step or wants to restart the process.
        The data of the wizard will be resetted before rendering the first step.
        """
        self.storage.reset()

        # reset the current step to the first step.
        self.storage.current_step = self.steps.first
        return self.render(self.get_forms())

    def post(self, *args, **kwargs):
        """
        This method handles POST requests.

        The wizard will render either the current step (if form validation
        wasn't successful), the next step (if the current step was stored
        successful) or the done view (if no more steps are available)
        """
        # Look for a wizard_goto_step element in the posted data which
        # contains a valid step name. If one was found, render the requested
        # form. (This makes stepping back a lot easier).
        wizard_goto_step = self.request.POST.get('wizard_goto_step', None)
        if wizard_goto_step and wizard_goto_step in self.get_form_list():
            return self.render_goto_step(wizard_goto_step)

        # Check if form was refreshed
        management_form = ManagementForm(self.request.POST, prefix=self.prefix)
        if not management_form.is_valid():
            raise ValidationError(
                _('ManagementForm data is missing or has been tampered.'),
                code='missing_management_form',
            )

        form_current_step = management_form.cleaned_data['current_step']
        if (form_current_step != self.steps.current and
                self.storage.current_step is not None):
            # form refreshed, change current step
            self.storage.current_step = form_current_step

        # get the form for the current step
        forms = self.get_forms(data=self.request.POST, files=self.request.FILES)

        # and try to validate
        all_valid = True
        for form in forms:
            if not form.is_valid():
                all_valid = False

        if all_valid:
            # if the form is valid, store the cleaned data and files.
            self.storage.set_step_data(self.steps.current, self.process_step(form))
            self.storage.set_step_files(self.steps.current, self.process_step_files(form))

            # check if the current step is the last step
            if self.steps.current == self.steps.last:
                # no more steps, render done view
                return self.render_done(form, **kwargs)
            else:
                # proceed to the next step
                return self.render_next_step(form)

        return self.render(forms)

    def get_forms(self, step=None, data=None, files=None):
        """
        Constructs the form for a given `step`. If no `step` is defined, the
        current step will be determined automatically.

        The form will be initialized using the `data` argument to prefill the
        new form. If needed, instance or queryset (for `ModelForm` or
        `ModelFormSet`) will be added too.
        """
        if step is None:
            step = self.steps.current
        form_struct = self.form_list[step]
        # prepare the kwargs for the form instance.

        form_collection = []
        if isinstance(form_struct, dict):
            initial_dict = self.get_form_initial(step)
            instance_dict = self.get_form_instance(step)
            form_collection = []
            for form_name, form_class in form_struct.items():
                initial = initial_dict.get(form_name, None) if initial_dict else None
                instance = instance_dict.get(form_name, None) if instance_dict else None
                kwargs = self.get_form_kwargs(step)
                kwargs.update({
                    'data': data,
                    'files': files,
                    'prefix': self.get_form_prefix(step, form_class),
                    'initial': initial
                })
                kwargs.setdefault('instance', instance)
                form = form_class(**kwargs)
                form._tag = form_name
                form_collection.append(form)
        elif issubclass(form_struct, (forms.ModelForm, forms.models.BaseInlineFormSet)):
            # If the form is based on ModelForm or InlineFormSet,
            # add instance if available and not previously set.
            form_class = form_struct
            kwargs = self.get_form_kwargs(step)
            kwargs.update({
                'data': data,
                'files': files,
                'prefix': self.get_form_prefix(step, form_class),
                'initial': self.get_form_initial(step)
            })
            kwargs.setdefault('instance', self.get_form_instance(step))
            form_collection = [form_class(**kwargs)]
        elif issubclass(form_struct, forms.Form):
            form_class = form_struct
            kwargs = self.get_form_kwargs(step)
            kwargs.update({
                'data': data,
                'files': files,
                'prefix': self.get_form_prefix(step, form_class),
                'initial': self.get_form_initial(step)
            })
            form_collection = [form_class(**kwargs)]
        elif issubclass(form_struct, forms.models.BaseModelFormSet):
            # If the form is based on ModelFormSet, add queryset if available
            # and not previous set.
            form_class = form_struct
            kwargs = self.get_form_kwargs(step)
            kwargs.update({
                'data': data,
                'files': files,
                'prefix': self.get_form_prefix(step, form_class),
                'initial': self.get_form_initial(step)
            })
            kwargs.setdefault('queryset', self.get_form_instance(step))
            form_collection = [form_class(**kwargs)]
        return form_collection

    def get_context_data(self, forms, **kwargs):
        """
        Returns the template context for a step. You can overwrite this method
        to add more data for all or some steps. This method returns a
        dictionary containing the rendered form step. Available template
        context variables are:

         * all extra data stored in the storage backend
         * `wizard` - a dictionary representation of the wizard instance

        Example:

        .. code-block:: python

            class MyWizard(WizardView):
                def get_context_data(self, form, **kwargs):
                    context = super(MyWizard, self).get_context_data(form=form, **kwargs)
                    if self.steps.current == 'my_step_name':
                        context.update({'another_var': True})
                    return context
        """
        if 'view' not in kwargs:
            kwargs['view'] = self
        context = kwargs
        context.update(self.storage.extra_data)
        context['wizard'] = {
            'forms': forms,
            'steps': self.steps,
            'management_form': ManagementForm(prefix=self.prefix, initial={
                'current_step': self.steps.current,
            }),
        }
        return context


class MultipleFormSessionWizardView(MultipleFormWizardView):
    """
    A WizardView with pre-configured SessionStorage backend.
    """
    storage_name = 'django.contrib.formtools.wizard.storage.session.SessionStorage'


class CookieWizardView(MultipleFormWizardView):
    """
    A WizardView with pre-configured CookieStorage backend.
    """
    storage_name = 'django.contrib.formtools.wizard.storage.cookie.CookieStorage'
    

class NamedUrlWizardView(MultipleFormWizardView):
    """
    A WizardView with URL named steps support.
    """
    url_name = None
    done_step_name = None

    @classmethod
    def get_initkwargs(cls, *args, **kwargs):
        """
        We require a url_name to reverse URLs later. Additionally users can
        pass a done_step_name to change the URL name of the "done" view.
        """
        assert 'url_name' in kwargs, 'URL name is needed to resolve correct wizard URLs'
        extra_kwargs = {
            'done_step_name': kwargs.pop('done_step_name', 'done'),
            'url_name': kwargs.pop('url_name'),
        }
        initkwargs = super(NamedUrlWizardView, cls).get_initkwargs(*args, **kwargs)
        initkwargs.update(extra_kwargs)

        assert initkwargs['done_step_name'] not in initkwargs['form_list'], \
            'step name "%s" is reserved for "done" view' % initkwargs['done_step_name']
        return initkwargs

    def get_step_url(self, step):
        return reverse(self.url_name, kwargs={'step': step})

    def get(self, *args, **kwargs):
        """
        This renders the form or, if needed, does the http redirects.
        """
        step_url = kwargs.get('step', None)
        if step_url is None:
            if 'reset' in self.request.GET:
                self.storage.reset()
                self.storage.current_step = self.steps.first
            if self.request.GET:
                query_string = "?%s" % self.request.GET.urlencode()
            else:
                query_string = ""
            return redirect(self.get_step_url(self.steps.current)
                            + query_string)

        # is the current step the "done" name/view?
        elif step_url == self.done_step_name:
            last_step = self.steps.last
            return self.render_done(self.get_form(step=last_step,
                data=self.storage.get_step_data(last_step),
                files=self.storage.get_step_files(last_step)
            ), **kwargs)

        # is the url step name not equal to the step in the storage?
        # if yes, change the step in the storage (if name exists)
        elif step_url == self.steps.current:
            # URL step name and storage step name are equal, render!
            return self.render(self.get_form(
                data=self.storage.current_step_data,
                files=self.storage.current_step_files,
            ), **kwargs)

        elif step_url in self.get_form_list():
            self.storage.current_step = step_url
            return self.render(self.get_form(
                data=self.storage.current_step_data,
                files=self.storage.current_step_files,
            ), **kwargs)

        # invalid step name, reset to first and redirect.
        else:
            self.storage.current_step = self.steps.first
            return redirect(self.get_step_url(self.steps.first))

    def post(self, *args, **kwargs):
        """
        Do a redirect if user presses the prev. step button. The rest of this
        is super'd from WizardView.
        """
        wizard_goto_step = self.request.POST.get('wizard_goto_step', None)
        if wizard_goto_step and wizard_goto_step in self.get_form_list():
            return self.render_goto_step(wizard_goto_step)
        return super(NamedUrlWizardView, self).post(*args, **kwargs)

    def get_context_data(self, form, **kwargs):
        """
        NamedUrlWizardView provides the url_name of this wizard in the context
        dict `wizard`.
        """
        context = super(NamedUrlWizardView, self).get_context_data(form=form, **kwargs)
        context['wizard']['url_name'] = self.url_name
        return context

    def render_next_step(self, form, **kwargs):
        """
        When using the NamedUrlWizardView, we have to redirect to update the
        browser's URL to match the shown step.
        """
        next_step = self.get_next_step()
        self.storage.current_step = next_step
        return redirect(self.get_step_url(next_step))

    def render_goto_step(self, goto_step, **kwargs):
        """
        This method gets called when the current step has to be changed.
        `goto_step` contains the requested step to go to.
        """
        self.storage.current_step = goto_step
        return redirect(self.get_step_url(goto_step))

    def render_revalidation_failure(self, failed_step, form, **kwargs):
        """
        When a step fails, we have to redirect the user to the first failing
        step.
        """
        self.storage.current_step = failed_step
        return redirect(self.get_step_url(failed_step))

    def render_done(self, form, **kwargs):
        """
        When rendering the done view, we have to redirect first (if the URL
        name doesn't fit).
        """
        if kwargs.get('step', None) != self.done_step_name:
            return redirect(self.get_step_url(self.done_step_name))
        return super(NamedUrlWizardView, self).render_done(form, **kwargs)


class NamedUrlSessionWizardView(NamedUrlWizardView):
    """
    A NamedUrlWizardView with pre-configured SessionStorage backend.
    """
    storage_name = 'django.contrib.formtools.wizard.storage.session.SessionStorage'


class NamedUrlCookieWizardView(NamedUrlWizardView):
    """
    A NamedUrlFormWizard with pre-configured CookieStorageBackend.
    """
    storage_name = 'django.contrib.formtools.wizard.storage.cookie.CookieStorage'

    
