#!/usr/bin/python

import cgi
import logging
import urllib2
from django.utils import simplejson
from google.appengine.api import users
from google.appengine.ext import db
from google.appengine.ext import webapp
from google.appengine.ext.webapp.util import run_wsgi_app
import os
from google.appengine.ext.webapp import template
import datetime
import time
import calendar

# prod
config = {'server':'https://foursquare.com',
          'api_server':'https://api.foursquare.com',
          'redirect_uri': 'https://4sqmatch.appspot.com/oauth',
          'client_id': '52AMDL0AMLMSQVYNG3FQYBXM4AF4N1THF5RBSQE5SIVQ4KOB',
          'client_secret': 'MMNBUFQBGX5SAOLXF4U002FJFKLG2V41HSRNY5W1S1C1QOBJ'}

class FS_User(db.Model):
  """Contains the user to foursquare_id + oauth token mapping."""
  fs_id = db.StringProperty()
  token = db.StringProperty()
  fs_firstName = db.StringProperty()
  fs_lastName = db.StringProperty()
  fs_photo = db.StringProperty()
  fs_gender = db.StringProperty()
  fs_homeCity = db.StringProperty()
  fs_email = db.StringProperty()
  fs_twitter = db.StringProperty()
  fs_checkins_count = db.IntegerProperty()
  user_place_index = db.StringListProperty()
  last_updated = db.DateTimeProperty(auto_now_add=True)

class FS_Place(db.Model):
  """A very simple checkin object, with a denormalized userid for querying."""
  fs_id = db.StringProperty()
  fs_name = db.StringProperty()
  fs_user_id_list = db.StringListProperty()

def fetchJson(url, dobasicauth = False):
  """Does a GET to the specified URL and returns a dict representing its reply."""
  logging.info('fetching url: ' + url)
  result = urllib2.urlopen(url).read()
  logging.info('got back: ' + result)
  return simplejson.loads(result)

class OAuth(webapp.RequestHandler):
  """Handle the OAuth redirect back to the service."""
  def post(self):
    self.get()

  def get(self):
    code = self.request.get('code')
    args = dict(config)
    args['code'] = code
    url = ('%(server)s/oauth2/access_token?client_id=%(client_id)s&client_secret=%(client_secret)s&grant_type=authorization_code&redirect_uri=%(redirect_uri)s&code=%(code)s' % args)
    json = fetchJson(url, config['server'].find('foursquare.com') != 1)
    self_response = fetchJson('%s/v2/users/self?oauth_token=%s' % (config['api_server'], json['access_token']))

    # if the person exists, pull all, else pull the diff
    numberToPull = 0
    currentUser = FS_User()
    currentUserId = str(self_response['response']['user']['id'])
    
    if FS_User.get_by_key_name(currentUserId) == None:
      currentUser = FS_User(key_name=currentUserId)
      updateUser(currentUser, json, self_response)
      numberToPull = currentUser.fs_checkins_count
    else:
      currentUser = FS_User.get_by_key_name(currentUserId)
      numberToPull = currentUser.fs_checkins_count
      updateUser(currentUser, json, self_response)
      numberToPull = currentUser.fs_checkins_count - numberToPull

    # now update the check-ins
    logging.info("going to pull " + str(numberToPull) + " checkins")
    if numberToPull > 0:
      updateCheckins(numberToPull, currentUser, 0)

    # Ok now take that list of this user's places, and see which have overlap
    listOfPeopleOverlap = {}
    
    for item in currentUser.user_place_index:
      # go through the places and get the list of people
      currentCheckinList = FS_Place.get_by_key_name(item).fs_user_id_list
      # Add each person along with a list of the places in common
      for person in currentCheckinList:
        if person != currentUser.fs_id:
          if person not in listOfPeopleOverlap:
            personDict = {}
            personDict['user'] = FS_User.get_by_key_name(person)
            personDict['places_list'] = {item:1}
            personDict['places_count'] = 1
            listOfPeopleOverlap[person] = personDict
          else:
            # check to see if its there before adding it.  if it is there, just increment the count
            listOfPeopleOverlap[person]['places_list']
            if item not in listOfPeopleOverlap[person]['places_list']:
              listOfPeopleOverlap[person]['places_list'][item] = 1
              listOfPeopleOverlap[person]['places_count'] += 1
            else:
              listOfPeopleOverlap[person]['places_list'][item] += 1
      
    # sort the list by popularity, with number
    # for k, v in listOfPeopleOverlap.items():
    
    doRender(self, 'results.html', {'profile_photo' : currentUser.fs_photo,
                                    'places' : listOfPeopleOverlap} )    

def updateUser(currentUser, json, self_response):
  currentUser.token             = json['access_token']
  currentUser.fs_id             = self_response['response']['user']['id']
  currentUser.fs_firstName      = self_response['response']['user']['firstName']
  currentUser.fs_lastName       = self_response['response']['user']['lastName']
  currentUser.fs_photo          = self_response['response']['user']['photo'].replace('_thumbs','')
  currentUser.fs_gender         = self_response['response']['user']['gender']
  currentUser.fs_homeCity       = self_response['response']['user']['homeCity']
  currentUser.fs_email          = self_response['response']['user']['contact']['email']
  currentUser.fs_checkins_count = self_response['response']['user']['checkins']['count']
  # TODO add some sort of check before adding twitter
  currentUser.put()
   
def updateCheckins(numberToPull, currentUser, offset):

  pullThisBatch = 0
  if numberToPull > 100:
    pullThisBatch = 100
  else:
    pullThisBatch = numberToPull
  
  json = fetchJson('%s/v2/users/self/checkins?limit=%s&offset=%s&oauth_token=%s' % (config['api_server'], pullThisBatch, offset, currentUser.token))  
  venue_list = json['response']['checkins']['items']

  for item in venue_list:
    if 'venue' in item:
      key_str = item['venue']['id']
      if FS_Place.get_by_key_name(key_str) == None:
        myCheckin = FS_Place(key_name=key_str)
        myCheckin.fs_name = item['venue']['name']
        myCheckin.fs_id = item['venue']['id']
        myCheckin.fs_user_id_list.append(currentUser.fs_id)
        myCheckin.put()
      else:
        myCheckin = FS_Place.get_by_key_name(key_str)
        myCheckin.fs_user_id_list.append(currentUser.fs_id)
        myCheckin.put()
      if key_str not in currentUser.user_place_index:
        currentUser.user_place_index.append(key_str)
  currentUser.put()
    
  if (numberToPull - pullThisBatch) > 0:
    updateCheckins((numberToPull - pullThisBatch), currentUser, (offset + pullThisBatch))
  else:
    return

class GetConfig(webapp.RequestHandler):
  """Returns the OAuth URI as JSON so the constants aren't in two places."""
  def get(self):
    uri = '%(server)s/oauth2/authenticate?client_id=%(client_id)s&response_type=code&redirect_uri=%(redirect_uri)s' % config
    self.response.out.write(simplejson.dumps({'auth_uri': uri}))

# A helper to do the rendering and to add the necessary
# variables for the _base.htm template
def doRender(handler, tname = 'index.htm', values = { }):
  temp = os.path.join(
      os.path.dirname(__file__),
      'templates/' + tname)
  if not os.path.isfile(temp):
    return False

  # Make a copy of the dictionary and add the path and session
  newval = dict(values)
  newval['path'] = handler.request.path
  outstr = template.render(temp, newval)
  handler.response.out.write(outstr)
  return True
  
application = webapp.WSGIApplication([('/oauth', OAuth), 
                                      ('/config', GetConfig)],
                                     debug=True)

def main():
  run_wsgi_app(application)

if __name__ == "__main__":
  main()
