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
import operator

# prod
config = {'server':'https://foursquare.com',
          'api_server':'https://api.foursquare.com',
          'redirect_uri': 'https://4sqmatch.appspot.com/oauth',
          'client_id': '52AMDL0AMLMSQVYNG3FQYBXM4AF4N1THF5RBSQE5SIVQ4KOB',
          'client_secret': 'MMNBUFQBGX5SAOLXF4U002FJFKLG2V41HSRNY5W1S1C1QOBJ'}

class FS_User(db.Model):
  # Contains the user to foursquare_id + oauth token mapping
  token = db.StringProperty()
  fs_id = db.StringProperty()
  fs_firstName = db.StringProperty()
  fs_lastName = db.StringProperty()
  fs_photo = db.StringProperty()
  fs_gender = db.StringProperty()
  fs_homeCity = db.StringProperty()
  fs_email = db.EmailProperty()
  fs_twitter = db.StringProperty()
  fs_checkins_count = db.IntegerProperty()
  user_place_index = db.StringListProperty()
  last_updated = db.DateTimeProperty(auto_now_add=True)
  list_people_overlap = db.StringListProperty()

  @property
  def get_overlaps(self):
    q = User_Overlap.all()
    q.filter("my_key =", self.fs_id)
    q.order("-total_places_count")
    return(q.fetch(limit=10))
    
class FS_Place(db.Model):
  # A foursquare place, with the list of people who've been there
  fs_name = db.StringProperty()
  fs_user_id_list = db.StringListProperty()

class User_Place_Count(db.Model):
  # A simple count of checkins for a user at a place
  place_count = db.IntegerProperty()

class Me_Them_Count(db.Model):
  # The overlap between the current user, another user, and a single place
  my_count = db.IntegerProperty()
  their_count = db.IntegerProperty()
  combined_count = db.IntegerProperty()
  place_key = db.StringProperty()

  @property
  def get_place(self):
    return(FS_Place.get_by_key_name(self.place_key))
     
class User_Overlap (db.Model):
  total_places_list = db.StringListProperty()
  total_places_count = db.IntegerProperty()
  their_key = db.StringProperty()
  my_key = db.StringProperty()
  
  @property
  def get_all_places(self):
    listOfPlaces = []
    for place in self.total_places_list:
      listOfPlaces.append(Me_Them_Count.get_by_key_name(place))
    sortedList = sorted(listOfPlaces, key=operator.attrgetter('combined_count'))
    sortedList.reverse()
    return sortedList
    
  @property
  def get_user(self):
    return(FS_User.get_by_key_name(self.their_key))

def fetchJson(url, dobasicauth = False):
  # Does a GET to the specified URL and returns a dict representing its reply
  logging.info('fetching url: ' + url)
  result = urllib2.urlopen(url).read()
  logging.info('got back: ' + result)
  return simplejson.loads(result)

class OAuth(webapp.RequestHandler):
  # Handle the OAuth redirect back to the service
  def post(self):
    self.get()

  def get(self):
    
    ######### DANGER! this empties the datastore ##############
    # query = db.GqlQuery("SELECT * FROM FS_Place")
    # for q in query:
    #   db.delete(q)
    # query = db.GqlQuery("SELECT * FROM User_Place_Count")
    # for q in query:
    #   db.delete(q)
    # query = db.GqlQuery("SELECT * FROM FS_User")
    # for q in query:
    #   db.delete(q)
    # query = db.GqlQuery("SELECT * FROM Me_Them_Count")
    # for q in query:
    #   db.delete(q)
    # query = db.GqlQuery("SELECT * FROM User_Overlap")
    # for q in query:
    #   db.delete(q)
    # currentUser = FS_User()
    ############################################################
    
    code = self.request.get('code')
    args = dict(config)
    args['code'] = code
    url = ('%(server)s/oauth2/access_token?client_id=%(client_id)s&client_secret=%(client_secret)s&grant_type=authorization_code&redirect_uri=%(redirect_uri)s&code=%(code)s' % args)
    json = fetchJson(url, config['server'].find('foursquare.com') != 1)
    self_response = fetchJson('%s/v2/users/self?oauth_token=%s' % (config['api_server'], json['access_token']))

    # If the person exists, pull info, else pull the diff
    timestamp = 0
    currentUser = FS_User()
    currentUserId = str(self_response['response']['user']['id'])

    if FS_User.get_by_key_name(currentUserId) == None:
     currentUser = FS_User(key_name=currentUserId)
     updateUser(currentUser, json, self_response)
    else:
     currentUser = FS_User.get_by_key_name(currentUserId)
     timestamp = currentUser.last_updated
     updateUser(currentUser, json, self_response)

    # now update the history
    if timestamp != 0:
     timestamp = calendar.timegm(timestamp.timetuple())
    logging.info("timestamp is " + str(timestamp))
    updateHistory(timestamp, currentUser)

    # Ok now take that list of this user's places, and see which have overlap    
    for place in currentUser.user_place_index:
     # go through the places and get the list of people
     visitors = FS_Place.get_by_key_name(place).fs_user_id_list
     # Add each person along with a list of the places in common
     for visitor in visitors:
       if visitor != currentUser.fs_id:
         # create a user overlap object
         combinedKey = currentUser.fs_id + "-" + place + "-" + visitor
         newMeThemCount = Me_Them_Count.get_or_insert(combinedKey)
         newMeThemCount.place_key = place
         newMeThemCount.my_count = User_Place_Count.get_by_key_name(currentUser.fs_id + "-" + place).place_count
         newMeThemCount.their_count = User_Place_Count.get_by_key_name(visitor + "-" + place).place_count
         newMeThemCount.combined_count = newMeThemCount.my_count + newMeThemCount.their_count
         newMeThemCount.put()
         userOverlapKey = currentUser.fs_id + "-" + visitor
         newUserOverlap = User_Overlap.get_or_insert(userOverlapKey)
         if visitor not in currentUser.list_people_overlap:
           # if the the person isn't yet known to be someone with overlap, add them
           newUserOverlap.total_places_list = [combinedKey]
           newUserOverlap.total_places_count = 1
           newUserOverlap.their_key = visitor
           newUserOverlap.my_key = currentUser.fs_id
           currentUser.list_people_overlap.append(visitor)
         else:
           # ok the visitor is in the list, now add the place
           if combinedKey not in newUserOverlap.total_places_list:
             newUserOverlap.total_places_list.append(combinedKey)
             newUserOverlap.total_places_count += 1
         newUserOverlap.put()
    currentUser.put()

    doRender(self, 'results.html', {'current_user' : currentUser} )

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
  if 'twitter' in self_response['response']['user']['contact']:
    currentUser.fs_twitter = self_response['response']['user']['contact']['twitter']
  currentUser.put()
   
def updateHistory(timestamp, currentUser):
  json = fetchJson('%s/v2/users/self/venuehistory?afterTimestamp=%s&oauth_token=%s' % (config['api_server'], timestamp, currentUser.token))  
  venue_list = json['response']['venues']['items']

  for place in venue_list:
    key_str = place['venue']['id']
    combinedKey = currentUser.fs_id + "-" + key_str
    # add or update the association between the person and the place
    if User_Place_Count.get_by_key_name(combinedKey) == None:      
      newUserPlaceCount = User_Place_Count(key_name=combinedKey)
      newUserPlaceCount.place_count = place['beenHere']
      newUserPlaceCount.put()
    else:
      newUserPlaceCount = User_Place_Count.get_by_key_name(combinedKey)
      newUserPlaceCount.place_count += place['beenHere']
      newUserPlaceCount.put()
    # If the place isn't yet in the db, add it
    if FS_Place.get_by_key_name(key_str) == None:
      newPlace = FS_Place(key_name=key_str)
      newPlace.fs_name = place['venue']['name']
      newPlace.fs_user_id_list.append(currentUser.fs_id)
      newPlace.put()
    else:
      existingPlace = FS_Place.get_by_key_name(key_str)
      # if the place is in the db, but the current person isn't in there, add them to the list
      if currentUser.fs_id not in existingPlace.fs_user_id_list:
        existingPlace.fs_user_id_list.append(currentUser.fs_id)
        existingPlace.put()
    # and to wrap things up, we add the place to the user's personal list of places
    if key_str not in currentUser.user_place_index:
      currentUser.user_place_index.append(key_str)
  # update the time stamp so we know when this person last updated
  currentUser.last_updated = datetime.datetime.now()
  currentUser.put()

# class DeleteCurrentUser(webapp.RequestHandler):
#   def post(self):
#     # Create a class that lets people delete their account


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
